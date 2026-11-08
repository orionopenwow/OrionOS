//! Orion desktop shell (Tauri v2).
//!
//! * owns the Python daemon (see daemon.rs) — Orion is a single app, no consoles
//! * lives in the system tray: closing the window hides it; Quit is in the tray menu
//! * single instance: launching Orion again just brings the window back
//! * signed auto-updates from GitHub Releases (tauri-plugin-updater; UI in UpdateBanner.tsx)

mod daemon;

use daemon::{DaemonManager, DaemonStatus};
use serde::Serialize;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WindowEvent};
use tauri_plugin_opener::OpenerExt;

#[derive(Serialize)]
struct AppInfo {
    version: String,
    log_dir: String,
    packaged: bool,
}

#[tauri::command]
fn daemon_status(daemon: State<'_, DaemonManager>) -> DaemonStatus {
    daemon.status()
}

/// The daemon's per-start API token, read from its owner-only file (review B1). The UI attaches it to every call
/// to http://localhost:9120 and to the WebSocket handshake (src/lib/daemonAuth.ts). None until the daemon is up.
#[tauri::command]
fn daemon_token() -> Option<String> {
    daemon::read_token()
}

#[tauri::command]
fn restart_daemon(daemon: State<'_, DaemonManager>) {
    let d = daemon.inner().clone();
    std::thread::spawn(move || d.restart());
}

/// Called by the UI right before installing an update so the daemon releases its files and port.
#[tauri::command]
fn stop_daemon(daemon: State<'_, DaemonManager>) {
    daemon.stop(true);
}

#[tauri::command]
fn open_logs(app: AppHandle, daemon: State<'_, DaemonManager>) -> Result<(), String> {
    let dir = daemon.log_dir();
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    app.opener().open_path(dir.to_string_lossy(), None::<&str>).map_err(|e| e.to_string())
}

/// One-click Python install for first run (per-user, no admin) via winget.
#[tauri::command]
fn install_python(daemon: State<'_, DaemonManager>) -> Result<(), String> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let d = daemon.inner().clone();
        std::thread::spawn(move || {
            let status = std::process::Command::new("winget")
                .args([
                    "install", "-e", "--id", "Python.Python.3.12", "--scope", "user",
                    "--accept-package-agreements", "--accept-source-agreements",
                ])
                .creation_flags(0x0800_0000)
                .status();
            match status {
                Ok(s) if s.success() => d.restart(),
                _ => log::warn!("winget python install failed"),
            }
        });
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = daemon;
        Err("Install Python 3.10+ with your package manager, then press Retry.".into())
    }
}

#[tauri::command]
fn app_info(app: AppHandle, daemon: State<'_, DaemonManager>) -> AppInfo {
    AppInfo {
        version: app.package_info().version.to_string(),
        log_dir: daemon.log_dir().to_string_lossy().into_owned(),
        packaged: !cfg!(debug_assertions),
    }
}

/// Hide-to-tray instead of quitting when the window is closed.
#[tauri::command]
fn hide_window(app: AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.hide();
    }
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Show Orion", true, None::<&str>)?;
    let restart = MenuItem::with_id(app, "restart", "Restart Orion's brain", true, None::<&str>)?;
    let update = MenuItem::with_id(app, "update", "Check for updates", true, None::<&str>)?;
    let logs = MenuItem::with_id(app, "logs", "Open logs", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit Orion", true, None::<&str>)?;
    let sep = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&show, &restart, &update, &logs, &sep, &quit])?;

    let mut builder = TrayIconBuilder::with_id("orion")
        .tooltip("Orion")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => show_main(app),
            "restart" => {
                let d = app.state::<DaemonManager>().inner().clone();
                std::thread::spawn(move || d.restart());
            }
            "update" => {
                show_main(app);
                let _ = app.emit("orion://check-update", ());
            }
            "logs" => {
                let dir = app.state::<DaemonManager>().log_dir();
                let _ = std::fs::create_dir_all(&dir);
                let _ = app.opener().open_path(dir.to_string_lossy(), None::<&str>);
            }
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

pub fn run() {
    let app = tauri::Builder::default()
        // Must be the first plugin: a second launch focuses the existing window instead.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            daemon_status,
            daemon_token,
            restart_daemon,
            stop_daemon,
            open_logs,
            install_python,
            app_info,
            hide_window
        ])
        .setup(|app| {
            let manager = DaemonManager::new(app.handle().clone());
            app.manage(manager.clone());
            manager.start();
            // A compositor without a tray host (OrionOS kiosk / Sway) must not stop Orion from starting.
            if let Err(e) = build_tray(app.handle()) {
                log::warn!("tray unavailable: {e}");
            }
            watch_web_process(app.handle());
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // Orion is ambient: closing the window keeps it running in the tray.
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Orion");

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            app.state::<DaemonManager>().stop(true);
        }
    });
}

/// If WebKit's web process dies (for example a crash in a software GL rasteriser in a VM), the window would stay
/// black forever. On OrionOS (`ORION_EXTERNAL_DAEMON=1`, started by `orion-hud`'s restart loop) exit so the loop
/// restarts the HUD and, after repeated crashes, switches to safe software drawing; elsewhere just reload the page.
#[cfg(target_os = "linux")]
fn watch_web_process(app: &AppHandle) {
    use webkit2gtk::WebViewExt;
    let Some(win) = app.get_webview_window("main") else { return };
    let external = std::env::var("ORION_EXTERNAL_DAEMON").map(|v| v == "1").unwrap_or(false);
    let _ = win.with_webview(move |wv| {
        wv.inner().connect_web_process_terminated(move |view, reason| {
            log::error!("web process terminated: {reason:?}");
            if external {
                std::process::exit(3);
            } else {
                view.reload();
            }
        });
    });
}

#[cfg(not(target_os = "linux"))]
fn watch_web_process(_app: &AppHandle) {}
