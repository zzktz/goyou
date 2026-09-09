use auto_launch::{AutoLaunch, AutoLaunchBuilder};

fn launcher() -> Result<AutoLaunch, String> {
    let path = std::env::current_exe().map_err(|error| error.to_string())?;
    AutoLaunchBuilder::new()
        .set_app_name("GoYou")
        .set_app_path(&path.to_string_lossy())
        .build()
        .map_err(|error| error.to_string())
}

pub fn get() -> Result<bool, String> {
    launcher()?.is_enabled().map_err(|error| error.to_string())
}
pub fn set(enabled: bool) -> Result<(), String> {
    let launcher = launcher()?;
    if enabled {
        launcher.enable()
    } else {
        launcher.disable()
    }
    .map_err(|error| error.to_string())
}
