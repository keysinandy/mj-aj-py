//! Tauri 壳主进程:负责 spawn Python sidecar(clientd)并代理前后端 IPC。
//!
//! **端口发现**:sidecar 以动态端口(0/0)启动,把实际端口写进本地发现文件;
//! 壳轮询该文件,拿到真实端口后注入 webview(全局变量 + DOM 事件),前端据此
//! 覆盖 HTTP/WS 端点。这样端口被别的程序占用时不再硬失败,多个实例也不会
//! 抢同一个固定端口。
//!
//! **崩溃自愈**:带指数退避与上限的重启;只有子进程稳定存活过
//! [`STABLE_UPTIME`] 才清零失败计数,连续快速失败超过
//! [`MAX_CONSECUTIVE_FAILURES`] 即停止(旧实现固定端口 + 500ms 死循环重启,
//! 端口冲突时会无限刷屏)。连接状态由前端 ConnectionStatus/store 呈现。
//!
//! 业务逻辑全部在 Python 侧,此处只做进程生命周期与端口发现编排。

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde::Deserialize;
use tauri::{AppHandle, Manager, Runtime};
use tauri_plugin_shell::{process::CommandEvent, ShellExt};

const HOST: &str = "127.0.0.1";
/// 端口发现文件的轮询间隔与等待上限。
const PORTS_POLL_INTERVAL: Duration = Duration::from_millis(100);
const PORTS_WAIT_TIMEOUT: Duration = Duration::from_secs(10);
/// 崩溃重启退避:基值 × 2^(n-1) 并封顶。
const RESTART_BASE_DELAY: Duration = Duration::from_millis(500);
const RESTART_MAX_DELAY: Duration = Duration::from_secs(30);
/// 子进程存活超过该时长视为「稳定」,清零失败计数。
const STABLE_UPTIME: Duration = Duration::from_secs(5);
const MAX_CONSECUTIVE_FAILURES: u32 = 6;

/// clientd 写出的发现文件里的实际端口。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
struct ClientdPorts {
    http: u16,
    ws: u16,
}

/// 解析发现文件内容;端口 0(尚未绑定)或 http==ws 视为无效。
fn parse_ports(json: &str) -> Option<ClientdPorts> {
    let ports: ClientdPorts = serde_json::from_str(json).ok()?;
    if ports.http == 0 || ports.ws == 0 || ports.http == ports.ws {
        None
    } else {
        Some(ports)
    }
}

/// 发现文件路径:落在用户数据目录,不依赖 sidecar 的工作目录。
fn discovery_path(data_dir: &Path) -> PathBuf {
    data_dir.join("clientd.ports.json")
}

/// 崩溃重启退避时长:按连续失败次数指数增长并封顶。
fn restart_delay(consecutive_failures: u32) -> Duration {
    let exponent = consecutive_failures.saturating_sub(1).min(6);
    let millis = RESTART_BASE_DELAY.as_millis() as u64 * (1u64 << exponent);
    Duration::from_millis(millis).min(RESTART_MAX_DELAY)
}

/// 从发现文件读取有效端口;边轮询边观察子进程是否已退出。
///
/// 返回 `(ports, exited)`:读到端口则 `exited=false`;等待期间子进程终止则
/// `(None, true)`;超时未读到则 `(None, false)`。
fn wait_for_ports(
    discovery: &Path,
    events: &mut tauri::async_runtime::Receiver<CommandEvent>,
    timeout: Duration,
) -> (Option<ClientdPorts>, bool) {
    let deadline = Instant::now() + timeout;
    loop {
        if let Ok(text) = std::fs::read_to_string(discovery) {
            if let Some(ports) = parse_ports(&text) {
                return (Some(ports), false);
            }
        }
        match events.try_recv() {
            Ok(CommandEvent::Terminated(_)) => return (None, true),
            Ok(CommandEvent::Error(error)) => eprintln!("clientd sidecar: {error}"),
            Ok(_) => {}
            // try_recv 的 Empty/Disconnected 无法在此区分;用 is_closed 兜底
            // 判定「子进程已退出且通道关闭」,避免早退时白等整个超时。
            Err(_) => {
                if events.is_closed() {
                    return (None, true);
                }
            }
        }
        if Instant::now() >= deadline {
            return (None, false);
        }
        std::thread::sleep(PORTS_POLL_INTERVAL);
    }
}

/// 把发现的端口注入 webview:既留全局变量(供晚加载的脚本读取),也派发
/// DOM 事件(供已注册监听的页面即时应用)。事件在 DOM 就绪后触发 —— 若注入
/// 发生在页面加载前,直接在加载期派发会被随后的文档替换抹掉。仅注入整数
/// 端口,无注入风险。
fn notify_webview<R: Runtime>(app: &AppHandle<R>, ports: ClientdPorts) {
    let script = format!(
        "(function(){{var p={{http:{},ws:{},wsPath:\"/ws\"}};\
         window.__CLIENTD_PORTS__=p;\
         function fire(){{window.dispatchEvent(new CustomEvent(\"clientd-ports\",\
         {{detail:p}}));}}\
         if(document.readyState===\"loading\")\
         {{document.addEventListener(\"DOMContentLoaded\",fire,{{once:true}});}}\
         else{{fire();}}}})();",
        ports.http, ports.ws
    );
    match app.get_webview_window("main") {
        Some(window) => {
            if let Err(error) = window.eval(script) {
                eprintln!("无法向前端注入 clientd 端口: {error}");
            }
        }
        None => eprintln!("未找到主窗口,clientd 端口未能注入前端"),
    }
}

fn spawn_clientd<R: Runtime>(app: AppHandle<R>) {
    // `tauri dev` 通常没有打包好的 sidecar,允许开发者通过环境变量
    // 指向本地 Python/已构建 clientd;正式制品始终使用 externalBin。
    if cfg!(debug_assertions) && std::env::var_os("MJ_CLIENTD_SIDECAR").is_none() {
        return;
    }

    let data_dir = match app.path().app_data_dir() {
        Ok(dir) => dir,
        Err(error) => {
            eprintln!("无法解析 clientd 数据目录: {error}");
            return;
        }
    };
    if let Err(error) = std::fs::create_dir_all(&data_dir) {
        eprintln!("无法创建 clientd 数据目录 {data_dir:?}: {error}");
        return;
    }
    let discovery = discovery_path(&data_dir);
    let discovery_arg = discovery.to_string_lossy().into_owned();

    tauri::async_runtime::spawn(async move {
        let mut consecutive_failures: u32 = 0;
        loop {
            // 每次重启前清掉旧发现文件,避免读到上一实例的端口。
            let _ = std::fs::remove_file(&discovery);

            let command = if let Some(program) = std::env::var_os("MJ_CLIENTD_SIDECAR") {
                app.shell().command(program)
            } else {
                match app.shell().sidecar("bin/mj-clientd") {
                    Ok(command) => command,
                    Err(error) => {
                        eprintln!("无法创建 clientd sidecar: {error}");
                        return;
                    }
                }
            }
            .args([
                "--host",
                HOST,
                // 动态端口:由 clientd 自行挑选空闲端口,避免固定端口冲突。
                "--http-port",
                "0",
                "--ws-port",
                "0",
                "--discovery",
                &discovery_arg,
            ]);

            let (mut events, _child) = match command.spawn() {
                Ok(child) => child,
                Err(error) => {
                    eprintln!("无法启动 clientd sidecar: {error}");
                    return;
                }
            };
            let started = Instant::now();

            let (ports, exited) = wait_for_ports(&discovery, &mut events, PORTS_WAIT_TIMEOUT);
            match ports {
                Some(ports) => notify_webview(&app, ports),
                None if !exited => {
                    eprintln!("clientd 未在 {PORTS_WAIT_TIMEOUT:?} 内写出端口发现文件")
                }
                None => {}
            }

            if !exited {
                while let Some(event) = events.recv().await {
                    match event {
                        CommandEvent::Terminated(_) => break,
                        CommandEvent::Error(error) => {
                            eprintln!("clientd sidecar: {error}")
                        }
                        _ => {}
                    }
                }
            }

            if started.elapsed() >= STABLE_UPTIME {
                consecutive_failures = 0;
            } else {
                consecutive_failures += 1;
            }
            if consecutive_failures > MAX_CONSECUTIVE_FAILURES {
                eprintln!(
                    "clientd sidecar 连续 {consecutive_failures} 次异常退出,\
                     停止自动重启(避免端口冲突下的无限重启循环)"
                );
                return;
            }
            // 进程崩溃后给端口释放和前端重连留出退避窗口。
            std::thread::sleep(restart_delay(consecutive_failures.max(1)));
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            spawn_clientd(app.handle().clone());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("failed to run tauri app");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_ports_accepts_real_ports() {
        assert_eq!(
            parse_ports(r#"{"http":17320,"ws":17321}"#),
            Some(ClientdPorts {
                http: 17320,
                ws: 17321
            })
        );
    }

    #[test]
    fn parse_ports_rejects_zero_because_of_startup_race() {
        // 竞态下发现文件里可能是 ws=0;壳必须拒绝,而不是去连 0 端口。
        assert_eq!(parse_ports(r#"{"http":17320,"ws":0}"#), None);
        assert_eq!(parse_ports(r#"{"http":0,"ws":17321}"#), None);
    }

    #[test]
    fn parse_ports_rejects_missing_equal_and_out_of_range() {
        assert_eq!(parse_ports(r#"{"http":17320}"#), None);
        assert_eq!(parse_ports(r#"{"http":17320,"ws":17320}"#), None);
        assert_eq!(parse_ports(r#"{"http":70000,"ws":17321}"#), None);
        assert_eq!(parse_ports("not json"), None);
    }

    #[test]
    fn restart_delay_grows_exponentially_and_caps() {
        assert_eq!(restart_delay(1), Duration::from_millis(500));
        assert_eq!(restart_delay(2), Duration::from_millis(1000));
        assert_eq!(restart_delay(3), Duration::from_millis(2000));
        assert_eq!(restart_delay(100), RESTART_MAX_DELAY);
    }

    #[test]
    fn discovery_path_lives_in_data_dir() {
        assert_eq!(
            discovery_path(Path::new("/data/app")),
            PathBuf::from("/data/app/clientd.ports.json")
        );
    }

    fn scratch_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "mj-clientd-ports-test-{}-{tag}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn wait_for_ports_reads_valid_discovery_file() {
        let dir = scratch_dir("valid");
        let path = discovery_path(&dir);
        std::fs::write(&path, r#"{"http":18120,"ws":18121}"#).unwrap();
        let (_tx, mut rx) = tauri::async_runtime::channel::<CommandEvent>(1);

        let (ports, exited) = wait_for_ports(&path, &mut rx, Duration::from_secs(1));
        assert_eq!(
            ports,
            Some(ClientdPorts {
                http: 18120,
                ws: 18121
            })
        );
        assert!(!exited);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn wait_for_ports_times_out_on_zero_port_file() {
        let dir = scratch_dir("zero");
        let path = discovery_path(&dir);
        std::fs::write(&path, r#"{"http":18120,"ws":0}"#).unwrap();
        let (_tx, mut rx) = tauri::async_runtime::channel::<CommandEvent>(1);

        let (ports, exited) = wait_for_ports(&path, &mut rx, Duration::from_millis(200));
        assert_eq!(ports, None);
        assert!(!exited);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
