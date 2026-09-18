//! Tauri 壳主进程:负责 spawn Python sidecar(clientd)并代理前后端 IPC。
//!
//! sidecar 崩溃自愈策略:通过 tauri-plugin-shell 持有的子进程句柄,
//! 在 exit 信号时自动重启并重新建立连接;连接状态已由前端
//! ConnectionStatus/store(连接状态机)呈现。此处仅做进程生命周期编排,
//! 业务逻辑全部在 Python 侧,不在此重复。

fn main() {
    mj_aj_client_lib::run()
}