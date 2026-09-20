use crate::auto_launch;
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{Read, Write},
    net::{IpAddr, SocketAddr, TcpStream, ToSocketAddrs},
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
const SSH_HOST: &str = "lsj.proxy.123371.com";
const SSH_PORT: u16 = 12581;
const SSH_USER: &str = "root";
const SSH_KNOWN_HOSTS_FILE: &str = "known_hosts";
const GIT_PROXY: &str = "socks5h://127.0.0.1:7890";
const CONTROL_PLANE_URL: &str = "https://goyou.123371.com";
const DIAGNOSTIC_SAMPLE_COUNT: usize = 5;
const DIAGNOSTIC_AVERAGE_COUNT: usize = 3;
const LOCAL_SOCKS_VALIDATION_ATTEMPTS: usize = 3;
const LOCAL_SOCKS_RETRY_DELAY: Duration = Duration::from_millis(350);

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
    #[serde(rename = "relay_name", default)]
    pub relay_name: Option<String>,
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
    pub local_port: u16,
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
    pub proxy_running: bool,
    pub local_proxy_reachable: bool,
    pub upstream_reachable: Option<bool>,
    pub failure_kind: Option<String>,
    pub upstream_error: Option<String>,
    pub github_reachable: bool,
    pub latency_ms: u64,
    pub github_status: Option<u16>,
    pub google_reachable: bool,
    pub google_latency_ms: u64,
    pub google_status: Option<u16>,
    pub git_proxy_configured: bool,
    pub git_proxy_matches_tunnel: bool,
    pub system_proxy_enabled: bool,
    pub proxy_mode: Option<String>,
    pub analysis: String,
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
    let exists = Command::new("kill")
        .args(["-0", &pid.to_string()])
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if !exists {
        return false;
    }
    // A detached child can remain as a zombie until its parent reaps it.
    // kill -0 still succeeds for that PID, but it is no longer a running
    // proxy process and must not make shutdown report a false failure.
    Command::new("ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
        .map(|output| {
            let state = String::from_utf8_lossy(&output.stdout);
            !state.trim_start().starts_with('Z')
        })
        .unwrap_or(exists)
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

#[cfg(windows)]
fn process_command_line(pid: u32) -> Option<String> {
    // `wmic` is no longer included on recent Windows installations. PowerShell
    // and the CIM provider are available on supported Windows versions and let
    // us identify a stale GoYou/ProxySwitch SSH tunnel without touching an
    // unrelated process that happens to use the proxy port.
    let query = format!(
        "$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; if ($p) {{ $p.CommandLine }}"
    );
    hidden_command("powershell")
        .args([
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            &query,
        ])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .filter(|command| !command.is_empty())
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
fn matching_tunnel(pid: u32) -> bool {
    process_command_line(pid)
        .map(|command| {
            let command = command.to_ascii_lowercase();
            command.contains("ssh")
                && command.contains(&format!("{HOST}:{PORT}"))
                && command.contains(&format!("{SSH_USER}@{SSH_HOST}"))
                && command.contains(&SSH_PORT.to_string())
                && (command.contains("goyou_tunnel=1") || command.contains("proxyswitch_tunnel=1"))
        })
        .unwrap_or(false)
}

#[cfg(unix)]
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

#[cfg(windows)]
fn discover_existing_tunnel() -> Option<u32> {
    let output = hidden_command("netstat")
        .args(["-ano", "-p", "tcp"])
        .output()
        .ok()?;
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| {
            let fields: Vec<&str> = line.split_whitespace().collect();
            if fields.len() < 5
                || !fields[0].eq_ignore_ascii_case("TCP")
                || !fields[1].ends_with(&format!(":{PORT}"))
                || !fields[3].eq_ignore_ascii_case("LISTENING")
            {
                return None;
            }
            fields[4].parse::<u32>().ok()
        })
        .find(|pid| matching_tunnel(*pid))
}
#[cfg(unix)]
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
#[cfg(windows)]
fn discover_existing_singbox() -> Option<u32> {
    let output = hidden_command("netstat")
        .args(["-ano", "-p", "tcp"])
        .output()
        .ok()?;
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| {
            let fields: Vec<&str> = line.split_whitespace().collect();
            if fields.len() < 5
                || !fields[0].eq_ignore_ascii_case("TCP")
                || !fields[1].ends_with(&format!(":{PORT}"))
                || !fields[3].eq_ignore_ascii_case("LISTENING")
            {
                return None;
            }
            fields[4].parse::<u32>().ok()
        })
        .find(|pid| matching_singbox(*pid))
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
    let existing = discover_existing_singbox().or_else(discover_existing_legacy_singbox);
    if let Some(pid) = existing {
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
fn relay_label(relay_name: Option<&str>) -> String {
    relay_name
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .map(|name| format!("代理节点（{name}）"))
        .unwrap_or_else(|| "代理节点".to_string())
}

fn resolve_relay_host(host: &str, port: u16, relay_name: Option<&str>) -> Result<String, String> {
    let label = relay_label(relay_name);
    if host.parse::<IpAddr>().is_ok() {
        return Ok(host.to_owned());
    }
    let addresses = (host, port)
        .to_socket_addrs()
        .map_err(|_| format!("{label}地址解析失败，请检查网络或节点配置。"))?;
    addresses
        .filter_map(|address| match address.ip() {
            IpAddr::V4(ip) => Some(ip.to_string()),
            IpAddr::V6(_) => None,
        })
        .next()
        .ok_or_else(|| format!("{label}没有可用的 IPv4 地址。"))
}

fn ensure_relay_endpoint(host: &str, port: u16, relay_name: Option<&str>) -> Result<(), String> {
    let label = relay_label(relay_name);
    let addresses = (host, port)
        .to_socket_addrs()
        .map_err(|_| format!("{label}地址解析失败，请检查网络或节点配置。"))?;
    let mut last_error = None;
    for address in addresses {
        match TcpStream::connect_timeout(&address, Duration::from_secs(3)) {
            Ok(_) => return Ok(()),
            Err(error) => last_error = Some(error.to_string()),
        }
    }
    Err(format!(
        "{label} 当前不可用{}，未启用系统代理。",
        last_error
            .map(|error| format!("：{error}"))
            .unwrap_or_default()
    ))
}

fn validate_local_socks() -> Result<(), String> {
    let mut stream = TcpStream::connect_timeout(
        &format!("{HOST}:{PORT}")
            .parse::<SocketAddr>()
            .map_err(|error| format!("本地代理地址无效：{error}"))?,
        Duration::from_secs(2),
    )
    .map_err(|error| format!("本地代理未能接受连接：{error}"))?;
    stream
        .set_read_timeout(Some(Duration::from_secs(8)))
        .and_then(|_| stream.set_write_timeout(Some(Duration::from_secs(8))))
        .map_err(|error| format!("无法设置代理连接超时：{error}"))?;

    stream
        .write_all(&[0x05, 0x01, 0x00])
        .and_then(|_| {
            let mut response = [0; 2];
            stream.read_exact(&mut response)?;
            if response != [0x05, 0x00] {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::PermissionDenied,
                    "SOCKS5 握手被拒绝",
                ));
            }
            Ok(())
        })
        .map_err(|error| format!("本地代理握手失败：{error}"))?;

    let target = b"www.gstatic.com";
    let mut request = Vec::with_capacity(7 + target.len());
    request.extend_from_slice(&[0x05, 0x01, 0x00, 0x03, target.len() as u8]);
    request.extend_from_slice(target);
    request.extend_from_slice(&443u16.to_be_bytes());
    stream
        .write_all(&request)
        .map_err(|error| format!("代理节点连接失败：{error}"))?;

    let mut response = [0; 4];
    stream
        .read_exact(&mut response)
        .map_err(|error| format!("代理节点未返回连接结果：{error}"))?;
    if response[0] != 0x05 || response[1] != 0x00 {
        return Err(format!(
            "代理节点连接失败（SOCKS5 错误码 {}）。",
            response[1]
        ));
    }
    let address_length = match response[3] {
        0x01 => 4,
        0x03 => {
            let mut length = [0; 1];
            stream
                .read_exact(&mut length)
                .map_err(|error| format!("读取代理节点地址失败：{error}"))?;
            usize::from(length[0])
        }
        0x04 => 16,
        _ => return Err("代理节点返回了无效的 SOCKS5 地址类型。".into()),
    };
    let mut address = vec![0; address_length + 2];
    stream
        .read_exact(&mut address)
        .map_err(|error| format!("读取代理节点连接地址失败：{error}"))?;
    Ok(())
}

fn is_transient_local_socks_error(error: &str) -> bool {
    let normalized = error.to_ascii_lowercase();
    normalized.contains("resource temporarily unavailable")
        || normalized.contains("operation would block")
        || normalized.contains("timed out")
        || error.contains("超时")
}

fn validate_local_socks_with_retry() -> Result<(), String> {
    let mut last_error = None;
    for attempt in 0..LOCAL_SOCKS_VALIDATION_ATTEMPTS {
        match validate_local_socks() {
            Ok(()) => return Ok(()),
            Err(error) => {
                let retryable = is_transient_local_socks_error(&error);
                last_error = Some(error);
                if !retryable || attempt + 1 == LOCAL_SOCKS_VALIDATION_ATTEMPTS {
                    break;
                }
                thread::sleep(LOCAL_SOCKS_RETRY_DELAY);
            }
        }
    }
    Err(last_error.unwrap_or_else(|| "本地代理验证失败。".to_string()))
}

async fn probe_endpoint_samples(
    client: &reqwest::Client,
    url: &'static str,
    method: reqwest::Method,
) -> (Result<u16, String>, u64) {
    let mut samples: Vec<(u64, u16)> = Vec::with_capacity(DIAGNOSTIC_SAMPLE_COUNT);
    let mut last_error = None;
    for _ in 0..DIAGNOSTIC_SAMPLE_COUNT {
        let started = Instant::now();
        let outcome = client
            .request(method.clone(), url)
            .send()
            .await
            .map_err(|error| error.to_string())
            .and_then(|response| {
                let status = response.status();
                if status.is_success() || status.is_redirection() {
                    Ok(status.as_u16())
                } else {
                    Err(format!("HTTP {}", status.as_u16()))
                }
            });
        let elapsed_ms = started.elapsed().as_millis() as u64;
        match outcome {
            Ok(status) => samples.push((elapsed_ms, status)),
            Err(error) => last_error = Some(error),
        }
    }
    if samples.is_empty() {
        return (
            Err(last_error.unwrap_or_else(|| "未收到有效响应".to_string())),
            0,
        );
    }

    // Average the samples closest to the median so a one-off DNS, TCP or
    // TLS delay does not inflate the latency shown to the user.
    let median = {
        let mut values: Vec<u64> = samples.iter().map(|(latency, _)| *latency).collect();
        values.sort_unstable();
        values[values.len() / 2]
    };
    samples.sort_by_key(|(latency, _)| latency.abs_diff(median));
    let selected = samples
        .iter()
        .take(DIAGNOSTIC_AVERAGE_COUNT.min(samples.len()))
        .collect::<Vec<_>>();
    let average = selected.iter().map(|(latency, _)| *latency).sum::<u64>() / selected.len() as u64;
    let status = selected[0].1;
    (Ok(status), average)
}

fn configured_relay_endpoint() -> Option<(String, u16)> {
    let value =
        serde_json::from_str::<serde_json::Value>(&fs::read_to_string(singbox_config_file()).ok()?)
            .ok()?;
    value
        .get("outbounds")?
        .as_array()?
        .iter()
        .find(|outbound| outbound.get("tag").and_then(serde_json::Value::as_str) == Some("relay"))
        .and_then(|outbound| {
            Some((
                outbound.get("server")?.as_str()?.to_owned(),
                u16::try_from(outbound.get("server_port")?.as_u64()?).ok()?,
            ))
        })
}

fn bundled_rule_set_paths(app: &AppHandle) -> Result<(PathBuf, PathBuf), String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("无法定位 sing-box 规则目录：{error}"))?;
    let resource_rules = resource_dir.join("sing-box").join("rules");
    let development_rules = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("resources")
        .join("sing-box")
        .join("rules");
    let rules_dir = if resource_rules.is_dir() {
        resource_rules
    } else {
        development_rules
    };
    let geosite_cn = rules_dir.join("geosite-cn.srs");
    let geoip_cn = rules_dir.join("geoip-cn.srs");
    if !geosite_cn.is_file() || !geoip_cn.is_file() {
        return Err(format!(
            "客户端缺少国内分流规则，请重新安装或更新客户端：{}",
            rules_dir.display()
        ));
    }
    Ok((geosite_cn, geoip_cn))
}

fn singbox_config(
    relay_host: &str,
    lease: &ProxyLease,
    geosite_cn: &Path,
    geoip_cn: &Path,
) -> serde_json::Value {
    serde_json::json!({
        "log": { "level": "warn" },
        // Chinese domains use direct DNS. Other domains use encrypted DNS
        // through the relay so local DNS pollution does not break overseas
        // sites before the route rules are evaluated.
        "dns": {
            "servers": [
                {
                    "tag": "local",
                    "address": "223.5.5.5",
                    "detour": "direct"
                },
                {
                    "tag": "remote",
                    "address": "tls://1dot1dot1dot1.cloudflare-dns.com",
                    "address_resolver": "local",
                    "detour": "relay"
                }
            ],
            "rules": [
                { "rule_set": ["geosite-cn"], "server": "local" },
                { "domain_suffix": ["localhost", "local"], "server": "local" }
            ],
            "final": "remote",
            "strategy": "prefer_ipv4"
        },
        "inbounds": [{
            "type": "mixed",
            "tag": "local-proxy",
            "listen": HOST,
            "listen_port": PORT
        }],
        "outbounds": [
            {
                "type": "direct",
                "tag": "direct"
            },
            {
                "type": "shadowsocks",
                "tag": "relay",
                "server": relay_host,
                "server_port": lease.port,
                "method": lease.method,
                "password": lease.password
            }
        ],
        "route": {
            "rule_set": [
                {
                    "type": "local",
                    "tag": "geosite-cn",
                    "format": "binary",
                    "path": geosite_cn
                },
                {
                    "type": "local",
                    "tag": "geoip-cn",
                    "format": "binary",
                    "path": geoip_cn
                }
            ],
            "rules": [
                { "ip_is_private": true, "outbound": "direct" },
                { "rule_set": ["geosite-cn", "geoip-cn"], "outbound": "direct" },
                { "domain_suffix": ["localhost", "local"], "outbound": "direct" }
            ],
            "final": "relay"
        }
    })
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
    // Resolve the relay endpoint before enabling DNS-over-relay. Otherwise a
    // hostname relay could create a dependency cycle: remote DNS uses the
    // relay, while the relay itself still needs to be resolved.
    let relay_name = lease.relay_name.as_deref();
    let relay_host = resolve_relay_host(&lease.host, lease.port, relay_name)?;
    ensure_relay_endpoint(&relay_host, lease.port, relay_name)?;
    ensure_port_available()?;
    let config_path = singbox_config_file();
    fs::create_dir_all(config_path.parent().ok_or("invalid sing-box config path")?)
        .map_err(|e| format!("无法创建 sing-box 配置目录: {e}"))?;
    let (geosite_cn, geoip_cn) = bundled_rule_set_paths(&app)?;
    let config = singbox_config(&relay_host, lease, &geosite_cn, &geoip_cn);
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
        // The proxy stays detached after startup; keeping a stderr pipe here
        // would close the reader when this function returns and can terminate
        // sing-box on its next warning write.
        .stderr(Stdio::null());
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
            if let Err(error) = validate_local_socks_with_retry() {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("{error}，未启用系统代理。"));
            }
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
            return Err("sing-box 启动失败，请检查租约和本地配置。".into());
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
                "http=127.0.0.1:7890;https=127.0.0.1:7890",
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
        local_port: PORT,
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
        if !terminated.success() && alive(pid) {
            return Err("无法停止代理进程。".into());
        }
        #[cfg(unix)]
        let mut exited = false;
        #[cfg(unix)]
        for _ in 0..20 {
            if !alive(pid) {
                if p.pid == Some(pid) {
                    p.pid = None;
                }
                if p.singbox_pid == Some(pid) {
                    p.singbox_pid = None;
                }
                exited = true;
                break;
            }
            thread::sleep(Duration::from_millis(100));
        }
        #[cfg(unix)]
        if exited {
            continue;
        }
        #[cfg(unix)]
        if alive(pid) {
            let killed = Command::new("/bin/kill")
                .args(["-KILL", &pid.to_string()])
                .status()
                .map_err(|e| format!("无法强制停止代理进程：{e}"))?;
            #[cfg(unix)]
            if !killed.success() && alive(pid) {
                return Err("代理进程未能停止。".into());
            }
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

/// Recover settings left behind by a crash, force quit, or an OS shutdown.
///
/// The system proxy points at GoYou's local port, so leaving that setting
/// enabled after the app has gone away can make every browser request fail.
/// The normal exit hook handles graceful quits; this fallback is called during
/// the next startup and only acts when persisted runtime state or the GoYou
/// proxy endpoint indicates that a previous session was active.
pub fn recover_stale_proxy() {
    let p = read();
    let stale = p.proxy_enabled
        || p.pid.is_some()
        || p.singbox_pid.is_some()
        || system_proxy_enabled()
        || p.git_proxy_backup_saved;
    if !stale {
        return;
    }

    let _ = disable_goyou();
}

#[tauri::command]
pub fn get_goyou_status() -> Status {
    status()
}
#[tauri::command]
pub fn check_goyou_proxy() -> bool {
    let current = status();
    current.tunnel_running && validate_local_socks_with_retry().is_ok()
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
            proxy_running: false,
            local_proxy_reachable: false,
            upstream_reachable: None,
            failure_kind: Some("local_proxy".into()),
            upstream_error: None,
            github_reachable: false,
            latency_ms: 0,
            github_status: None,
            google_reachable: false,
            google_latency_ms: 0,
            google_status: None,
            git_proxy_configured: false,
            git_proxy_matches_tunnel: false,
            system_proxy_enabled: s.system_proxy_enabled,
            proxy_mode: s.proxy_mode,
            analysis: "本地代理进程未运行，无法通过代理检测站点。".into(),
            error: Some("本地代理进程未运行。".into()),
        };
    }
    let local_proxy_reachable = port_open();
    let (upstream_reachable, upstream_error) = match configured_relay_endpoint() {
        Some((host, port)) => match ensure_relay_endpoint(&host, port, None) {
            Ok(()) => (Some(true), None),
            Err(error) => (Some(false), Some(error)),
        },
        None if s.proxy_mode.as_deref() == Some("sing-box") => {
            (Some(false), Some("未找到 sing-box 上游节点配置。".into()))
        }
        None => (None, None),
    };
    let client = reqwest::Client::builder()
        // `socks5h` delegates hostname resolution to sing-box. Using plain
        // `socks5` would resolve the test domain with the local DNS first and
        // report a false failure on networks with broken DNS.
        .proxy(reqwest::Proxy::all(format!("socks5h://{HOST}:{PORT}")).unwrap())
        .user_agent("GoYou/1.0")
        .timeout(Duration::from_secs(15))
        .build();
    let (github_outcome, latency_ms, google_outcome, google_latency_ms) = match client {
        Ok(client) => {
            let github_request =
                probe_endpoint_samples(&client, "https://github.com/", reqwest::Method::HEAD);
            let google_request = probe_endpoint_samples(
                &client,
                "https://www.google.com/generate_204",
                reqwest::Method::GET,
            );
            let ((github_outcome, latency_ms), (google_outcome, google_latency_ms)) =
                tokio::join!(github_request, google_request);
            (
                github_outcome,
                latency_ms,
                google_outcome,
                google_latency_ms,
            )
        }
        Err(error) => {
            let detail = error.to_string();
            (Err(detail.clone()), 0, Err(detail), 0)
        }
    };
    let github_status = github_outcome.as_ref().ok().copied();
    let google_status = google_outcome.as_ref().ok().copied();
    let github_reachable = github_status.is_some();
    let google_reachable = google_status.is_some();
    let proxy_auth_failed = [&github_outcome, &google_outcome]
        .into_iter()
        .filter_map(|outcome| outcome.as_ref().err())
        .map(|error| error.to_ascii_lowercase())
        .any(|error| {
            error.contains("authentication")
                || error.contains("auth method")
                || error.contains("proxy auth")
                || error.contains("socks5 auth")
                || error.contains("407")
        });
    let failure_kind = if !local_proxy_reachable {
        Some("local_proxy".to_string())
    } else if upstream_reachable == Some(false) {
        Some("upstream".to_string())
    } else if proxy_auth_failed {
        Some("auth".to_string())
    } else if !github_reachable || !google_reachable {
        Some("target".to_string())
    } else {
        None
    };
    let analysis = match (github_reachable, google_reachable) {
        (true, true) => "GitHub 和 Google 均可达。".to_string(),
        (true, false) => "GitHub 可达但 Google 不可达，可能是当前网络或代理节点对 Google 域名、DNS、区域线路有限制。".to_string(),
        (false, true) => "Google 可达但 GitHub 不可达，可能是 GitHub 域名或代理节点线路受限。".to_string(),
        (false, false) => "GitHub 和 Google 均不可达，优先检查本地代理进程、代理上游节点和 DNS 解析。".to_string(),
    };
    let analysis = match failure_kind.as_deref() {
        Some("local_proxy") => "本地代理端口不可用，系统代理指向了一个未监听的本地服务。".into(),
        Some("upstream") => "本地代理端口可用，但上游代理节点不可达，无法继续转发请求。".into(),
        Some("auth") => "本地代理和上游节点可达，但代理认证失败，请刷新租约凭据。".into(),
        Some("target") => "本地代理和上游节点可用，但目标站点或 DNS 解析不可达。".into(),
        _ => analysis,
    };
    let errors = [
        upstream_error
            .clone()
            .map(|error| format!("上游节点：{error}")),
        github_outcome.err().map(|error| format!("GitHub: {error}")),
        google_outcome.err().map(|error| format!("Google: {error}")),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>();
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
        proxy_running: true,
        local_proxy_reachable,
        upstream_reachable,
        failure_kind,
        upstream_error,
        github_reachable,
        latency_ms,
        github_status,
        google_reachable,
        google_latency_ms,
        google_status,
        git_proxy_configured: configured,
        git_proxy_matches_tunnel: matches,
        system_proxy_enabled: s.system_proxy_enabled,
        proxy_mode: s.proxy_mode,
        analysis,
        error: (!errors.is_empty()).then(|| errors.join("；")),
    }
}

#[tauri::command]
pub async fn get_goyou_usage(access_token: String) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::builder()
        .no_proxy()
        .user_agent("GoYou/1.0")
        .timeout(Duration::from_secs(12))
        .build()
        .map_err(|error| format!("无法创建管理服务器请求：{error}"))?;
    let response = client
        .get(format!("{CONTROL_PLANE_URL}/v1/usage/today"))
        .bearer_auth(access_token)
        .send()
        .await
        .map_err(|error| format!("无法连接管理服务器，请检查网络或稍后重试：{error}"))?;
    let status = response.status();
    let body = response
        .text()
        .await
        .map_err(|error| format!("读取管理服务器响应失败：{error}"))?;
    let value = serde_json::from_str::<serde_json::Value>(&body)
        .map_err(|error| format!("管理服务器返回数据无效：{error}"))?;
    if !status.is_success() {
        let detail = value
            .get("detail")
            .and_then(serde_json::Value::as_str)
            .map(str::to_owned)
            .unwrap_or_else(|| status.to_string());
        return Err(detail);
    }
    Ok(value)
}

#[tauri::command]
pub async fn control_request(
    path: String,
    method: String,
    body: Option<serde_json::Value>,
    access_token: Option<String>,
) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::builder()
        .no_proxy()
        .user_agent("GoYou/1.0")
        .timeout(Duration::from_secs(12))
        .build()
        .map_err(|error| format!("无法创建管理服务器请求：{error}"))?;
    let method = reqwest::Method::from_bytes(method.to_uppercase().as_bytes())
        .map_err(|error| format!("无效的管理请求方法：{error}"))?;
    let mut request = client.request(method, format!("{CONTROL_PLANE_URL}{path}"));
    if let Some(token) = access_token.filter(|token| !token.trim().is_empty()) {
        request = request.bearer_auth(token);
    }
    if let Some(body) = body {
        request = request.header("content-type", "application/json").body(
            serde_json::to_vec(&body).map_err(|error| format!("序列化管理请求失败：{error}"))?,
        );
    }
    let response = request
        .send()
        .await
        .map_err(|error| format!("无法连接管理服务器，请检查网络或稍后重试：{error}"))?;
    let status = response.status();
    let body = response
        .text()
        .await
        .map_err(|error| format!("读取管理服务器响应失败：{error}"))?;
    let value = if body.trim().is_empty() {
        serde_json::Value::Null
    } else {
        serde_json::from_str::<serde_json::Value>(&body)
            .map_err(|error| format!("管理服务器返回数据无效：{error}"))?
    };
    if !status.is_success() {
        let detail = value
            .get("detail")
            .and_then(serde_json::Value::as_str)
            .map(str::to_owned)
            .unwrap_or_else(|| status.to_string());
        return Err(detail);
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::{ensure_relay_endpoint, singbox_config, ProxyLease};
    use std::path::Path;

    fn config() -> serde_json::Value {
        let lease = ProxyLease {
            relay_name: Some("测试 Relay".into()),
            host: "relay.example.com".into(),
            port: 443,
            method: "aes-256-gcm".into(),
            password: "test-password".into(),
            expires_at: "2099-01-01T00:00:00Z".into(),
        };
        singbox_config(
            "203.0.113.10",
            &lease,
            Path::new("/app/rules/geosite-cn.srs"),
            Path::new("/app/rules/geoip-cn.srs"),
        )
    }

    #[test]
    fn config_contains_direct_and_relay_outbounds() {
        let value = config();
        let outbounds = value["outbounds"].as_array().expect("outbounds array");
        assert!(outbounds.iter().any(|item| item["tag"] == "direct"));
        assert!(outbounds.iter().any(|item| item["tag"] == "relay"));
        assert_eq!(value["route"]["final"], "relay");
    }

    #[test]
    fn config_routes_china_rule_sets_directly() {
        let value = config();
        let rule_sets = value["route"]["rule_set"]
            .as_array()
            .expect("route rule_set array");
        assert_eq!(rule_sets[0]["tag"], "geosite-cn");
        assert_eq!(rule_sets[0]["format"], "binary");
        assert_eq!(rule_sets[1]["tag"], "geoip-cn");
        assert_eq!(rule_sets[1]["format"], "binary");

        let route_rules = value["route"]["rules"]
            .as_array()
            .expect("route rules array");
        assert_eq!(route_rules[0]["ip_is_private"], true);
        assert_eq!(route_rules[0]["outbound"], "direct");
        assert_eq!(route_rules[1]["rule_set"][0], "geosite-cn");
        assert_eq!(route_rules[1]["rule_set"][1], "geoip-cn");
        assert_eq!(route_rules[1]["outbound"], "direct");
    }

    #[test]
    fn config_splits_chinese_and_remote_dns() {
        let value = config();
        assert_eq!(value["dns"]["final"], "remote");
        let dns_rules = value["dns"]["rules"].as_array().expect("dns rules array");
        assert_eq!(dns_rules[0]["rule_set"][0], "geosite-cn");
        assert_eq!(dns_rules[0]["server"], "local");
        assert_eq!(value["dns"]["servers"][0]["detour"], "direct");
        assert_eq!(value["dns"]["servers"][1]["detour"], "relay");
    }

    #[test]
    fn relay_endpoint_errors_use_name_without_address() {
        let error = ensure_relay_endpoint("127.0.0.1", 1, Some("真实代理服务器 Relay"))
            .expect_err("port 1 should not accept a connection");
        assert!(error.contains("代理节点（真实代理服务器 Relay） 当前不可用"));
        assert!(!error.contains("127.0.0.1:1"));
    }
}
