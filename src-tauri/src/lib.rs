mod auto_launch;
mod commands;
use tauri::Manager;

pub fn run() {
    tauri::Builder::default()
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

            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_goyou_status,
            commands::enable_goyou,
            commands::disable_goyou,
            commands::diagnose_goyou,
            commands::get_goyou_usage,
            commands::control_request,
            commands::set_auto_launch,
            commands::get_auto_launch_status,
            commands::set_goyou_git_proxy
        ])
        .run(tauri::generate_context!())
        .expect("failed to run GoYou");
}
