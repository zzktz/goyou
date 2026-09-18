mod auto_launch;
mod commands;
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, RunEvent, WindowEvent,
};

const SHOW_WINDOW_MENU_ID: &str = "show-window";
const TOGGLE_PROXY_MENU_ID: &str = "toggle-proxy";
const QUIT_MENU_ID: &str = "quit";

fn show_main_window(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            #[cfg(target_os = "macos")]
            {
                // Tauri's default macOS menu derives these labels from the
                // executable's localized name, which can be lower-cased by
                // macOS even though the product name is "GoYou". Keep the
                // standard menu actions, but explicitly preserve the brand's
                // casing in the visible labels.
                if let Some(menu) = app.menu() {
                    if let Some(app_menu) = menu.items()?.first().and_then(|item| item.as_submenu())
                    {
                        let items = app_menu.items()?;
                        for (index, label) in
                            [(0, "About GoYou"), (4, "Hide GoYou"), (7, "Quit GoYou")]
                        {
                            if let Some(item) = items
                                .get(index)
                                .and_then(|item| item.as_predefined_menuitem())
                            {
                                item.set_text(label)?;
                            }
                        }
                    }
                }
            }

            let show_window =
                MenuItem::with_id(app, SHOW_WINDOW_MENU_ID, "显示 GoYou", true, None::<&str>)?;
            let toggle_proxy = MenuItem::with_id(
                app,
                TOGGLE_PROXY_MENU_ID,
                "开启/关闭代理",
                true,
                None::<&str>,
            )?;
            let separator = PredefinedMenuItem::separator(app)?;
            let quit = MenuItem::with_id(app, QUIT_MENU_ID, "退出 GoYou", true, None::<&str>)?;
            let tray_menu =
                Menu::with_items(app, &[&show_window, &toggle_proxy, &separator, &quit])?;

            let tray_builder = TrayIconBuilder::with_id("goyou-tray")
                .menu(&tray_menu)
                .tooltip("GoYou")
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    SHOW_WINDOW_MENU_ID => show_main_window(app),
                    TOGGLE_PROXY_MENU_ID => {
                        let _ = app.emit("tray:toggle-proxy", ());
                    }
                    QUIT_MENU_ID => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main_window(tray.app_handle());
                    }
                });

            #[cfg(target_os = "macos")]
            // Keep this source dependency so tray asset updates force a new app binary.
            let tray_builder = tray_builder
                .icon(tauri::include_image!(
                    "icons/tray/macos/statusbar_template_3x.png"
                ))
                .icon_as_template(true);
            #[cfg(not(target_os = "macos"))]
            let tray_builder = tray_builder.icon(
                app.default_window_icon()
                    .cloned()
                    .expect("missing default application icon"),
            );
            // Keep the tray resource alive for the lifetime of the application.
            // Dropping it removes the status-bar item on macOS.
            std::mem::forget(tray_builder.build(app)?);

            if let Some(window) = app.get_webview_window("main") {
                let window_for_events = window.clone();
                window.on_window_event(move |event| {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = window_for_events.hide();
                    }
                });
                let _ = window.show();
            }

            // A forced quit or crash cannot run the normal exit hook. Recover
            // stale proxy state after showing the window so a slow Windows
            // taskkill or PowerShell process lookup cannot block startup.
            std::thread::spawn(commands::recover_stale_proxy);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_goyou_status,
            commands::check_goyou_proxy,
            commands::enable_goyou,
            commands::disable_goyou,
            commands::diagnose_goyou,
            commands::get_goyou_usage,
            commands::control_request,
            commands::set_auto_launch,
            commands::get_auto_launch_status,
            commands::set_goyou_git_proxy
        ])
        .build(tauri::generate_context!())
        .expect("failed to build GoYou")
        .run(|app, event| {
            // macOS sends `Reopen` when the user clicks the Dock icon while
            // the app is still running but its windows are hidden. Restore
            // the main window so the Dock behaves like the tray icon.
            #[cfg(target_os = "macos")]
            if matches!(
                &event,
                RunEvent::Reopen {
                    has_visible_windows: false,
                    ..
                }
            ) {
                show_main_window(app);
            }

            // Release the local proxy before the process exits. This is
            // especially important on Windows, where a running sing-box.exe
            // prevents the NSIS installer from replacing the bundled binary.
            if matches!(event, RunEvent::ExitRequested { .. }) {
                let _ = commands::disable_goyou();
            }
        });
}
