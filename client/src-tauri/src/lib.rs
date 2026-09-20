use std::time::Duration;

use tauri::{AppHandle, Runtime};
use tauri_plugin_shell::{process::CommandEvent, ShellExt};

fn spawn_clientd<R: Runtime>(app: AppHandle<R>) {
    // `tauri dev` 通常没有打包好的 sidecar，允许开发者通过环境变量
    // 指向本地 Python/已构建 clientd；正式制品始终使用 externalBin。
    if cfg!(debug_assertions) && std::env::var_os("MJ_CLIENTD_SIDECAR").is_none() {
        return;
    }

    tauri::async_runtime::spawn(async move {
        loop {
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
                "127.0.0.1",
                "--http-port",
                "17320",
                "--ws-port",
                "17321",
            ]);

            let (mut events, _child) = match command.spawn() {
                Ok(child) => child,
                Err(error) => {
                    eprintln!("无法启动 clientd sidecar: {error}");
                    return;
                }
            };
            while let Some(event) = events.recv().await {
                match event {
                    CommandEvent::Terminated(_) => break,
                    CommandEvent::Error(error) => eprintln!("clientd sidecar: {error}"),
                    _ => {}
                }
            }
            // 进程崩溃后给端口释放和前端重连留出短暂窗口。
            std::thread::sleep(Duration::from_millis(500));
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
