use crate::auto_launch;
use serde::{Deserialize, Serialize};
use std::{
    fs,
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::{AppHandle, Manager};

const HOST: &str = "127.0.0.1";
const PORT: u16 = 7890;
const SSH_HOST: &str = "154.21.84.35";
const SSH_PORT: u16 = 12581;
const SSH_USER: &str = "root";
const SSH_KNOWN_HOSTS_FILE: &str = "known_hosts";
const GIT_PROXY: &str = "socks5h://127.0.0.1:7890";

// A manual stop cancels any in-flight proxy operation.
static MANUAL_STOP_REQUESTED: AtomicBool = AtomicBool::new(false);
static TUNNEL_OPERATION: Mutex<()> = Mutex::new(());

#[derive(Default, Deserialize, Serialize)]
struct Preferences {
    proxy_enabled: bool,
    pid: Option<u32>,
    #[serde(default)]
    singbox_pid: Option<u32>,
    #[serde(default)]
    git_proxy_enabled: bool,
    #[serde(default)]
    git_proxy_backup_saved: bool,
    #[serde(default)]
    git_http_proxy_backup: Option<String>,
    #[serde(default)]
    git_https_proxy_backup: Option<String>,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProxyLease {
    pub host: String,
    pub port: u16,
    pub method: String,
    pub password: String,
    #[serde(rename = "expires_at")]
    pub expires_at: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub state: String,
    pub tunnel_running: bool,
    pub port_listening: bool,
    pub system_proxy_enabled: bool,
    pub proxy_enabled: bool,
    pub git_proxy_enabled: bool,
    pub last_error: Option<String>,
    pub proxy_mode: Option<String>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Diagnostic {
    pub github_reachable: bool,
    pub latency_ms: u64,
    pub git_proxy_configured: bool,
    pub git_proxy_matches_tunnel: bool,
    pub error: Option<String>,
}

fn goyou_dir() -> PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("GoYou")
}
fn legacy_dir() -> PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("ProxySwitch")
}
fn file() -> PathBuf {
    goyou_dir().join("settings.json")
}
fn legacy_file() -> PathBuf {
    legacy_dir().join("settings.json")
}
fn known_hosts_file() -> PathBuf {
    file()
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."))
        .join(SSH_KNOWN_HOSTS_FILE)
}
fn read() -> Preferences {
    let path = if file().is_file() {
        file()
    } else if legacy_file().is_file() {
        // Move the existing settings and runtime files to the GoYou directory
        // on first use, so upgrading does not reset the user's configuration.
        let _ = fs::create_dir_all(goyou_dir());
        for name in ["settings.json", SSH_KNOWN_HOSTS_FILE, "sing-box.json"] {
            let source = legacy_dir().join(name);
            let target = goyou_dir().join(name);
            if source.is_file() && !target.exists() {
                let _ = fs::copy(source, target);
            }
        }
        file()
    } else {
        file()
    };
    fs::read_to_string(path)
        .ok()
        .and_then(|v| serde_json::from_str(&v).ok())
        .unwrap_or_default()
}
fn save(value: &Preferences) -> Result<(), String> {
    let path = file();
    fs::create_dir_all(path.parent().ok_or("invalid settings path")?).map_err(|e| e.to_string())?;
    fs::write(path, serde_json::to_vec(value).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())
}
fn git_config_value(key: &str) -> Option<String> {
    git_command()
        .args(["config", "--global", "--get", key])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .filter(|value| !value.is_empty())
}
fn set_git_config(key: &str, value: &str) -> Result<(), String> {
    let status = git_command()
        .args(["config", "--global", key, value])
        .status()
        .map_err(|error| format!("无法设置 Git {key}: {error}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("无法设置 Git {key}。"))
    }
}
fn unset_git_config(key: &str) -> Result<(), String> {
    let status = git_command()
        .args(["config", "--global", "--unset", key])
        .status()
        .map_err(|error| format!("无法恢复 Git {key}: {error}"))?;
    if status.success() || git_config_value(key).is_none() {
        Ok(())
    } else {
        Err(format!("无法恢复 Git {key}。"))
    }
}
fn git_proxy_matches_tunnel() -> bool {
    git_config_value("http.proxy").as_deref() == Some(GIT_PROXY)
        && git_config_value("https.proxy").as_deref() == Some(GIT_PROXY)
}
fn enable_git_proxy(p: &mut Preferences) -> Result<(), String> {
    if !p.git_proxy_backup_saved {
        p.git_http_proxy_backup = git_config_value("http.proxy");
        p.git_https_proxy_backup = git_config_value("https.proxy");
        p.git_proxy_backup_saved = true;
        save(p)?;
    }
    if let Err(error) = set_git_config("http.proxy", GIT_PROXY)
        .and_then(|_| set_git_config("https.proxy", GIT_PROXY))
    {
        let _ = restore_git_proxy(p);
        return Err(error);
    }
    Ok(())
}
fn restore_git_proxy(p: &mut Preferences) -> Result<(), String> {
    if !p.git_proxy_backup_saved {
        return Ok(());
    }
    match p.git_http_proxy_backup.as_deref() {
        Some(value) => set_git_config("http.proxy", value)?,
        None => unset_git_config("http.proxy")?,
    }
    match p.git_https_proxy_backup.as_deref() {
        Some(value) => set_git_config("https.proxy", value)?,
        None => unset_git_config("https.proxy")?,
    }
    p.git_proxy_backup_saved = false;
    p.git_http_proxy_backup = None;
    p.git_https_proxy_backup = None;
    Ok(())
}
fn port_open() -> bool {
    format!("{HOST}:{PORT}")
        .parse::<SocketAddr>()
        .ok()
        .map(|a| TcpStream::connect_timeout(&a, Duration::from_millis(150)).is_ok())
        .unwrap_or(false)
}
#[cfg(unix)]
fn alive(pid: u32) -> bool {
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}
#[cfg(windows)]
fn hidden_command(program: &str) -> Command {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let mut command = Command::new(program);
    command.creation_flags(CREATE_NO_WINDOW);
    command
}

fn git_command() -> Command {
    #[cfg(windows)]
    {
        return hidden_command("git");
    }
    #[cfg(not(windows))]
    {
        Command::new("git")
    }
}

#[cfg(windows)]
fn alive(pid: u32) -> bool {
    hidden_command("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
        .output()
        .map(|output| {
            let listing = String::from_utf8_lossy(&output.stdout);
            listing.contains(&format!("\"{pid}\""))
        })
        .unwrap_or(false)
}
#[cfg(unix)]
fn matching_tunnel(pid: u32) -> bool {
    Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "command="])
        .output()
        .map(|output| {
            let command = String::from_utf8_lossy(&output.stdout);
            command.contains("ssh")
                && command.contains(&format!("{HOST}:{PORT}"))
                && command.contains(&format!("{SSH_USER}@{SSH_HOST}"))
                && command.contains(&SSH_PORT.to_string())
                && (command.contains("GOYOU_TUNNEL=1") || command.contains("PROXYSWITCH_TUNNEL=1"))
        })
        .unwrap_or(false)
}
#[cfg(unix)]
fn matching_singbox(pid: u32) -> bool {
    let config = singbox_config_file();
    Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "command="])
        .output()
        .map(|output| {
            let command = String::from_utf8_lossy(&output.stdout);
            command.contains("sing-box") && command.contains(&config.to_string_lossy().to_string())
        })
        .unwrap_or(false)
}
#[cfg(unix)]
fn matching_legacy_singbox(pid: u32) -> bool {
    let config = legacy_dir().join("sing-box.json");
    Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "command="])
        .output()
        .map(|output| {
            let command = String::from_utf8_lossy(&output.stdout);
            command.contains("sing-box") && command.contains(&config.to_string_lossy().to_string())
        })
        .unwrap_or(false)
}
#[cfg(windows)]
fn matching_singbox(pid: u32) -> bool {
    hidden_command("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
        .output()
        .map(|output| {
            String::from_utf8_lossy(&output.stdout)
                .to_ascii_lowercase()
                .contains("\"sing-box.exe\"")
        })
        .unwrap_or(false)
}
#[cfg(windows)]
fn matching_legacy_singbox(_pid: u32) -> bool {
    false
}
#[cfg(windows)]
fn matching_tunnel(_pid: u32) -> bool {
    false
}
fn discover_existing_tunnel() -> Option<u32> {
    Command::new("lsof")
        .args(["-nP", "-t", &format!("-iTCP:{PORT}"), "-sTCP:LISTEN"])
        .output()
        .ok()
        .and_then(|output| {
            String::from_utf8_lossy(&output.stdout)
                .lines()
                .next()?
                .parse()
                .ok()
        })
        .filter(|pid| matching_tunnel(*pid))
}
fn discover_existing_singbox() -> Option<u32> {
    Command::new("pgrep")
        .args([
            "-f",
            &format!("sing-box.*{}", singbox_config_file().to_string_lossy()),
        ])
        .output()
        .ok()
        .and_then(|output| {
            String::from_utf8_lossy(&output.stdout)
                .lines()
                .next()?
                .parse()
                .ok()
        })
        .filter(|pid| matching_singbox(*pid))
}
#[cfg(unix)]
fn discover_existing_legacy_singbox() -> Option<u32> {
    Command::new("lsof")
        .args(["-nP", "-t", &format!("-iTCP:{PORT}"), "-sTCP:LISTEN"])
        .output()
        .ok()
        .and_then(|output| {
            String::from_utf8_lossy(&output.stdout)
                .lines()
                .next()?
                .parse()
                .ok()
        })
        .filter(|pid| matching_legacy_singbox(*pid))
}
#[cfg(windows)]
fn discover_existing_legacy_singbox() -> Option<u32> {
    None
}
fn singbox_config_file() -> PathBuf {
    file()
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("sing-box.json")
}
fn bundled_singbox_path(app: &AppHandle) -> Option<PathBuf> {
    let resource_dir = app.path().resource_dir().ok()?.join("sing-box");
    ["sing-box", "sing-box.exe"]
        .into_iter()
        .map(|name| resource_dir.join(name))
        .find(|path| path.is_file())
}
fn terminate_process(pid: u32) -> Result<(), String> {
    #[cfg(unix)]
    {
        let terminated = Command::new("/bin/kill")
            .args(["-TERM", &pid.to_string()])
            .status()
            .map_err(|e| format!("无法停止代理进程：{e}"))?;
        if !terminated.success() {
            return Err("无法停止代理进程。".into());
        }
        for _ in 0..20 {
            if !alive(pid) {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(100));
        }
        let killed = Command::new("/bin/kill")
            .args(["-KILL", &pid.to_string()])
            .status()
            .map_err(|e| format!("无法强制停止代理进程：{e}"))?;
        if !killed.success() || alive(pid) {
            return Err("代理进程未能停止。".into());
        }
    }
    #[cfg(windows)]
    {
        let terminated = hidden_command("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .output()
            .map_err(|e| format!("无法停止代理进程：{e}"))?;
        if !terminated.status.success() && alive(pid) {
            return Err("代理进程未能停止。".into());
        }
    }
    Ok(())
}
fn ensure_port_available() -> Result<(), String> {
    if let Some(pid) = discover_existing_legacy_singbox() {
        terminate_process(pid)?;
    }
    if port_open() {
        return Err("127.0.0.1:7890 已被其他进程占用。".into());
    }
    Ok(())
}
fn singbox_program(app: &AppHandle) -> Result<PathBuf, String> {
    let configured = std::env::var("GOYOU_SING_BOX_PATH")
        .or_else(|_| std::env::var("PROXYSWITCH_SING_BOX_PATH"));
    if let Ok(configured) = configured {
        let configured = configured.trim();
        if !configured.is_empty() {
            let path = PathBuf::from(configured);
            if path.is_file() {
                return Ok(path);
            }
            return Err(format!(
                "GOYOU_SING_BOX_PATH 指向的文件不存在：{configured}"
            ));
        }
    }
    if let Some(path) = bundled_singbox_path(app) {
        return Ok(path);
    }
    Ok(PathBuf::from(if cfg!(windows) {
        "sing-box.exe"
    } else {
        "sing-box"
    }))
}
fn start_singbox(app: &AppHandle, lease: &ProxyLease) -> Result<(), String> {
    if lease.host.trim().is_empty()
        || lease.method.trim().is_empty()
        || lease.password.trim().is_empty()
    {
        return Err("代理租约缺少节点地址或密码。".into());
    }
    if status().tunnel_running {
        return Ok(());
    }
    ensure_port_available()?;
    let config_path = singbox_config_file();
    fs::create_dir_all(config_path.parent().ok_or("invalid sing-box config path")?)
        .map_err(|e| format!("无法创建 sing-box 配置目录: {e}"))?;
    let config = serde_json::json!({
        "log": { "level": "warn" },
        "inbounds": [{
            "type": "mixed",
            "tag": "local-proxy",
            "listen": HOST,
            "listen_port": PORT
        }],
        "outbounds": [{
            "type": "shadowsocks",
            "tag": "relay",
            "server": lease.host,
            "server_port": lease.port,
            "method": lease.method,
            "password": lease.password
        }],
        "route": { "final": "relay" }
    });
    fs::write(
        &config_path,
        serde_json::to_vec_pretty(&config).map_err(|e| e.to_string())?,
    )
    .map_err(|e| format!("无法写入 sing-box 配置: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut permissions = fs::metadata(&config_path)
            .map_err(|e| format!("无法读取 sing-box 配置权限: {e}"))?
            .permissions();
        permissions.set_mode(0o600);
        fs::set_permissions(&config_path, permissions)
            .map_err(|e| format!("无法保护 sing-box 配置: {e}"))?;
    }
    let program = singbox_program(app)?;
    #[cfg(unix)]
    if let Ok(metadata) = fs::metadata(&program) {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o111 == 0 {
            let mut permissions = metadata.permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(&program, permissions)
                .map_err(|e| format!("无法启用内置 sing-box：{e}"))?;
        }
    }
    let mut command = Command::new(&program);
    command
        .args(["run", "-c"])
        .arg(&config_path)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command.spawn().map_err(|e| {
        format!(
            "无法启动 sing-box（{}）。开发环境可安装 sing-box，发行版应包含内置客户端：{e}",
            program.display()
        )
    })?;
    for _ in 0..30 {
        if port_open() {
            let mut p = read();
            p.singbox_pid = Some(child.id());
            save(&p)?;
            return Ok(());
        }
        if child
            .try_wait()
            .map_err(|e| format!("检查 sing-box 状态失败: {e}"))?
            .is_some()
        {
            let output = child
                .wait_with_output()
                .map_err(|e| format!("读取 sing-box 错误信息失败: {e}"))?;
            let detail = String::from_utf8_lossy(&output.stderr).trim().to_string();
            return Err(if detail.is_empty() {
                "sing-box 启动失败，请检查租约和本地配置。".into()
            } else {
                format!("sing-box 启动失败：{detail}")
            });
        }
        thread::sleep(Duration::from_millis(200));
    }
    let _ = child.kill();
    Err("sing-box 未在 6 秒内监听本地端口。".into())
}
#[cfg(target_os = "macos")]
fn system_proxy_enabled() -> bool {
    Command::new("/usr/sbin/scutil")
        .arg("--proxy")
        .output()
        .map(|output| {
            let proxy = String::from_utf8_lossy(&output.stdout);
            proxy.contains("SOCKSEnable : 1")
                && proxy.contains(&format!("SOCKSProxy : {HOST}"))
                && proxy.contains(&format!("SOCKSPort : {PORT}"))
        })
        .unwrap_or(false)
}
#[cfg(not(any(target_os = "macos", windows)))]
fn system_proxy_enabled() -> bool {
    false
}
#[cfg(target_os = "macos")]
fn set_system_proxy(enabled: bool) -> Result<bool, String> {
    let services = Command::new("/usr/sbin/networksetup")
        .arg("-listallnetworkservices")
        .output()
        .map_err(|e| format!("无法读取网络服务: {e}"))?;
    if !services.status.success() {
        return Err("无法读取网络服务。".into());
    }

    let service_output = String::from_utf8_lossy(&services.stdout);
    let names: Vec<_> = service_output
        .lines()
        .skip(1)
        .map(str::trim)
        .filter(|name| !name.is_empty() && !name.starts_with('*'))
        .map(str::to_owned)
        .collect();
    if names.is_empty() {
        return Err("未找到可用网络服务。".into());
    }

    let port = PORT.to_string();
    for name in names {
        if enabled {
            let configured = Command::new("/usr/sbin/networksetup")
                .args(["-setsocksfirewallproxy", &name, HOST, &port])
                .status()
                .map_err(|e| format!("无法配置 {name} 的 SOCKS 代理: {e}"))?;
            if !configured.success() {
                return Err(format!("无法配置 {name} 的 SOCKS 代理。"));
            }
        }
        let state = if enabled { "on" } else { "off" };
        let updated = Command::new("/usr/sbin/networksetup")
            .args(["-setsocksfirewallproxystate", &name, state])
            .status()
            .map_err(|e| format!("无法更新 {name} 的 SOCKS 代理状态: {e}"))?;
        if !updated.success() {
            return Err(format!("无法更新 {name} 的 SOCKS 代理状态。"));
        }
    }
    Ok(system_proxy_enabled())
}
#[cfg(windows)]
fn system_proxy_enabled() -> bool {
    const KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings";
    let Ok(output) = hidden_command("reg.exe")
        .args(["query", KEY, "/v", "ProxyEnable"])
        .output()
    else {
        return false;
    };
    let enable_output = String::from_utf8_lossy(&output.stdout).to_ascii_lowercase();
    let enabled = enable_output
        .lines()
        .any(|line| line.contains("proxyenable") && line.contains("0x1"));
    if !enabled {
        return false;
    }
    let Ok(output) = hidden_command("reg.exe")
        .args(["query", KEY, "/v", "ProxyServer"])
        .output()
    else {
        return false;
    };
    String::from_utf8_lossy(&output.stdout)
        .to_ascii_lowercase()
        .lines()
        .any(|line| line.contains("proxyserver") && line.contains("127.0.0.1:7890"))
}

#[cfg(windows)]
fn set_system_proxy(enabled: bool) -> Result<bool, String> {
    const KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings";
    if enabled {
        let status = hidden_command("reg.exe")
            .args([
                "add",
                KEY,
                "/v",
                "ProxyServer",
                "/t",
                "REG_SZ",
                "/d",
                "socks=127.0.0.1:7890",
                "/f",
            ])
            .status()
            .map_err(|e| format!("无法配置 Windows 系统代理: {e}"))?;
        if !status.success() {
            return Err("无法配置 Windows 系统代理。".into());
        }
    }
    let state = if enabled { "1" } else { "0" };
    let status = hidden_command("reg.exe")
        .args([
            "add",
            KEY,
            "/v",
            "ProxyEnable",
            "/t",
            "REG_DWORD",
            "/d",
            state,
            "/f",
        ])
        .status()
        .map_err(|e| format!("无法更新 Windows 系统代理状态: {e}"))?;
    if !status.success() {
        return Err("无法更新 Windows 系统代理状态。".into());
    }
    let _ = hidden_command("rundll32.exe")
        .args(["user32.dll,UpdatePerUserSystemParameters"])
        .status();
    Ok(system_proxy_enabled())
}

#[cfg(not(any(target_os = "macos", windows)))]
fn set_system_proxy(_enabled: bool) -> Result<bool, String> {
    Ok(false)
}
fn status() -> Status {
    let mut p = read();
    if p.pid.is_none() && port_open() {
        p.pid = discover_existing_tunnel();
        let _ = save(&p);
    }
    if p.singbox_pid.is_none() && port_open() {
        p.singbox_pid = discover_existing_singbox();
        let _ = save(&p);
    }
    let process = p.pid.map(alive).unwrap_or(false);
    if !process {
        p.pid = None;
        let _ = save(&p);
    }
    let singbox_process = p.singbox_pid.map(alive).unwrap_or(false);
    if !singbox_process {
        p.singbox_pid = None;
        let _ = save(&p);
    }
    let listening = port_open();
    let ssh_running = process && matching_tunnel(p.pid.unwrap_or_default()) && listening;
    let singbox_running =
        singbox_process && matching_singbox(p.singbox_pid.unwrap_or_default()) && listening;
    let running = ssh_running || singbox_running;
    let error = if p.proxy_enabled && !running {
        Some("代理已启用但本地代理进程未运行。".to_string())
    } else if listening && !process && !singbox_process {
        Some("7890 端口已被其他进程占用。".to_string())
    } else {
        None
    };
    Status {
        state: if running && p.proxy_enabled {
            "on"
        } else if p.proxy_enabled && error.is_some() {
            "error"
        } else {
            "off"
        }
        .into(),
        tunnel_running: running,
        port_listening: listening,
        system_proxy_enabled: system_proxy_enabled(),
        proxy_enabled: p.proxy_enabled,
        git_proxy_enabled: p.git_proxy_enabled || git_proxy_matches_tunnel(),
        last_error: error,
        proxy_mode: if singbox_running {
            Some("sing-box".into())
        } else if ssh_running {
            Some("ssh".into())
        } else {
            None
        },
    }
}
fn start() -> Result<(), String> {
    if status().tunnel_running {
        return Ok(());
    }
    ensure_port_available()?;
    let known_hosts = known_hosts_file();
    fs::create_dir_all(known_hosts.parent().ok_or("invalid known hosts path")?)
        .map_err(|e| format!("无法创建 SSH 配置目录: {e}"))?;
    let mut args = vec![
        "-N".to_string(),
        "-D".to_string(),
        format!("{HOST}:{PORT}"),
        "-p".to_string(),
        SSH_PORT.to_string(),
        "-o".to_string(),
        "BatchMode=yes".to_string(),
        "-o".to_string(),
        "StrictHostKeyChecking=accept-new".to_string(),
        "-o".to_string(),
        format!("UserKnownHostsFile={}", known_hosts.to_string_lossy()),
        "-o".to_string(),
        "ExitOnForwardFailure=yes".to_string(),
        "-o".to_string(),
        "ConnectTimeout=10".to_string(),
        "-o".to_string(),
        "SetEnv=GOYOU_TUNNEL=1".to_string(),
    ];
    args.push(format!("{SSH_USER}@{SSH_HOST}"));

    let mut command = Command::new("ssh");
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("启动代理连接失败，请确认客户端网络环境：{e}"))?;
    for _ in 0..30 {
        if port_open() {
            let mut p = read();
            p.pid = Some(child.id());
            save(&p)?;
            return Ok(());
        }
        if child
            .try_wait()
            .map_err(|e| format!("检查代理连接状态失败：{e}"))?
            .is_some()
        {
            let output = child
                .wait_with_output()
                .map_err(|e| format!("读取 SSH 错误信息失败: {e}"))?;
            let detail = String::from_utf8_lossy(&output.stderr).trim().to_string();
            return Err(if detail.is_empty() {
                "代理连接启动失败，请检查网络和服务器连接。".into()
            } else {
                format!("代理连接启动失败：{detail}")
            });
        }
        thread::sleep(Duration::from_millis(200));
    }
    let _ = child.kill();
    Err("代理连接未在 6 秒内监听本地端口。".into())
}
fn stop() -> Result<(), String> {
    let mut p = read();
    let pids = [p.pid, p.singbox_pid];
    for pid in pids.into_iter().flatten().filter(|id| alive(*id)) {
        #[cfg(unix)]
        let terminated = Command::new("/bin/kill")
            .args(["-TERM", &pid.to_string()])
            .status()
            .map_err(|e| format!("无法停止代理连接：{e}"))?;
        #[cfg(unix)]
        if !terminated.success() {
            return Err("无法停止代理进程。".into());
        }
        #[cfg(unix)]
        for _ in 0..20 {
            if !alive(pid) {
                if p.pid == Some(pid) {
                    p.pid = None;
                }
                if p.singbox_pid == Some(pid) {
                    p.singbox_pid = None;
                }
                continue;
            }
            thread::sleep(Duration::from_millis(100));
        }
        #[cfg(unix)]
        let killed = Command::new("/bin/kill")
            .args(["-KILL", &pid.to_string()])
            .status()
            .map_err(|e| format!("无法强制停止代理进程：{e}"))?;
        #[cfg(unix)]
        if !killed.success() || alive(pid) {
            return Err("代理进程未能停止。".into());
        }
        #[cfg(windows)]
        {
            let terminated = Command::new("taskkill")
                .args(["/PID", &pid.to_string(), "/T", "/F"])
                .output()
                .map_err(|e| format!("无法停止代理进程：{e}"))?;
            if !terminated.status.success() && alive(pid) {
                return Err("代理进程未能停止。".into());
            }
        }
    }
    p.pid = None;
    p.singbox_pid = None;
    save(&p)
}
#[tauri::command]
pub fn get_goyou_status() -> Status {
    status()
}
#[tauri::command]
pub fn set_auto_launch(enabled: bool) -> Result<bool, String> {
    auto_launch::set(enabled)?;
    Ok(enabled)
}
#[tauri::command]
pub fn get_auto_launch_status() -> Result<bool, String> {
    auto_launch::get()
}
#[tauri::command]
pub fn set_goyou_git_proxy(enabled: bool) -> Result<bool, String> {
    let mut p = read();
    let previous = p.git_proxy_enabled;
    p.git_proxy_enabled = enabled;
    let result = if enabled && p.proxy_enabled {
        enable_git_proxy(&mut p)
    } else if !enabled {
        if p.git_proxy_backup_saved {
            restore_git_proxy(&mut p)
        } else if git_proxy_matches_tunnel() {
            unset_git_config("http.proxy").and_then(|_| unset_git_config("https.proxy"))
        } else {
            Ok(())
        }
    } else {
        Ok(())
    };
    if let Err(error) = result {
        p.git_proxy_enabled = previous;
        let _ = save(&p);
        return Err(error);
    }
    save(&p)?;
    Ok(enabled)
}
#[tauri::command]
pub fn enable_goyou(app: AppHandle, lease: Option<ProxyLease>) -> Result<Status, String> {
    MANUAL_STOP_REQUESTED.store(false, Ordering::SeqCst);
    let _operation = TUNNEL_OPERATION
        .lock()
        .map_err(|_| "代理操作锁异常。".to_string())?;
    if let Some(lease) = lease.as_ref() {
        start_singbox(&app, lease)?;
    } else {
        start()?;
    }
    if let Err(e) = set_system_proxy(true) {
        let _ = stop();
        return Err(e);
    }
    let mut p = read();
    if p.git_proxy_enabled {
        if let Err(error) = enable_git_proxy(&mut p) {
            let _ = set_system_proxy(false);
            let _ = stop();
            return Err(error);
        }
    }
    p.proxy_enabled = true;
    save(&p)?;
    Ok(status())
}
#[tauri::command]
pub fn disable_goyou() -> Result<Status, String> {
    MANUAL_STOP_REQUESTED.store(true, Ordering::SeqCst);
    let _operation = TUNNEL_OPERATION
        .lock()
        .map_err(|_| "代理操作锁异常。".to_string())?;
    let system_proxy_result = set_system_proxy(false);
    let mut p = read();
    let git_proxy_result = restore_git_proxy(&mut p);
    p.proxy_enabled = false;
    save(&p)?;
    stop()?;
    system_proxy_result?;
    git_proxy_result?;
    Ok(status())
}
#[tauri::command]
pub async fn diagnose_goyou() -> Diagnostic {
    let s = status();
    if !s.tunnel_running {
        return Diagnostic {
            github_reachable: false,
            latency_ms: 0,
            git_proxy_configured: false,
            git_proxy_matches_tunnel: false,
            error: Some("本地代理进程未运行。".into()),
        };
    }
    let client = reqwest::Client::builder()
        .proxy(reqwest::Proxy::all(format!("socks5://{HOST}:{PORT}")).unwrap())
        .user_agent("GoYou/1.0")
        .timeout(Duration::from_secs(15))
        .build();
    let started = Instant::now();
    let github_outcome = match client {
        Ok(c) => c
            .head("https://github.com/")
            .send()
            .await
            .map(|_| ())
            .map_err(|e| e.to_string()),
        Err(e) => Err(e.to_string()),
    };
    let github_reachable = github_outcome.is_ok();
    let error = github_outcome.err();
    let proxies: [String; 2] = ["http.proxy", "https.proxy"].map(|k| {
        Command::new("git")
            .args(["config", "--global", "--get", k])
            .output()
            .ok()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_owned())
            .unwrap_or_default()
    });
    let configured = proxies.iter().any(|v| !v.is_empty());
    let matches = proxies.iter().any(|v| v.contains("127.0.0.1:7890"));
    Diagnostic {
        github_reachable,
        latency_ms: started.elapsed().as_millis() as u64,
        git_proxy_configured: configured,
        git_proxy_matches_tunnel: matches,
        error,
    }
}
