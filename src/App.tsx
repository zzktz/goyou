import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import appPackage from "../package.json";
import {
  getSession,
  getTodayUsage,
  login,
  logout,
  refreshSession,
} from "./auth";
import type { AuthSession, UsageSummary } from "./auth";

interface Status {
  state: "on" | "off" | "error";
  tunnelRunning: boolean;
  portListening: boolean;
  systemProxyEnabled: boolean;
  proxyEnabled: boolean;
  gitProxyEnabled: boolean;
  lastError: string | null;
  proxyMode: "sing-box" | "ssh" | null;
}

interface Diagnostic {
  githubReachable: boolean;
  latencyMs: number;
  gitProxyConfigured: boolean;
  gitProxyMatchesTunnel: boolean;
  error: string | null;
}

function AuthPage({
  onAuthenticated,
}: {
  onAuthenticated: (session: AuthSession) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    const normalizedEmail = email.trim();
    if (!normalizedEmail || !normalizedEmail.includes("@")) {
      setError("请输入有效的邮箱地址");
      return;
    }
    if (password.length < 8) {
      setError("密码至少需要 8 位");
      return;
    }
    setBusy(true);
    try {
      const session = await login(normalizedEmail, password);
      onAuthenticated(session);
    } catch (submissionError) {
      setError(
        submissionError instanceof Error
          ? submissionError.message
          : String(submissionError),
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand">
          <div className="brand-mark" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <div>
            <div className="brand-heading">
              <strong>GoYou</strong>
              <span className="brand-version">v{appPackage.version}</span>
            </div>
            <small>穿越无形的墙，去你心之所向。</small>
          </div>
        </div>
        <div className="auth-intro">
          <h1 id="auth-title">用户登录</h1>
        </div>
        <form className="auth-form" onSubmit={(event) => void submit(event)}>
          <label>
            账号
            <input
              autoComplete="email"
              inputMode="email"
              required
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
            />
          </label>
          <label>
            密码
            <input
              autoComplete="current-password"
              required
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="至少 8 位字符"
            />
          </label>
          <div className="form-row">
            <span />
            <button
              className="text-button"
              type="button"
              onClick={() => setError("密码找回功能将在管理服务器上线后开放")}
            >
              忘记密码？
            </button>
          </div>
          {error && (
            <p className="auth-error" role="alert">
              {error}
            </p>
          )}
          <button className="submit-button" disabled={busy} type="submit">
            {busy ? "处理中…" : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}

function Dashboard({
  session,
  onLogout,
  onSessionRefreshed,
}: {
  session: AuthSession;
  onLogout: () => void;
  onSessionRefreshed: (session: AuthSession) => void;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [autoLaunch, setAutoLaunch] = useState(false);
  const [busy, setBusy] = useState(false);
  const [busyAction, setBusyAction] = useState<
    "enable" | "disable" | "network" | null
  >(null);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [message, setMessage] = useState("尚未检测网络连通性");
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const autoClosedDate = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    const [next, launch] = await Promise.all([
      invoke<Status>("get_goyou_status"),
      invoke<boolean>("get_auto_launch_status"),
    ]);
    setStatus(next);
    setAutoLaunch(launch);
    try {
      setUsage(await getTodayUsage(session));
    } catch {
      // Usage reporting is optional while the control plane is unavailable.
    }
  }, [session]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshSession(session)
        .then((nextSession) => {
          onSessionRefreshed(nextSession);
          setMessage("登录令牌和代理租约已刷新");
        })
        .catch((error) => setMessage(String(error)));
    }, 10 * 60_000);
    return () => window.clearInterval(timer);
  }, [onSessionRefreshed, session]);

  const enable = async () => {
    if (!status) return;
    if (usage?.exceeded) {
      setMessage("今日流量额度已用尽，代理将在明日 00:00 后恢复");
      return;
    }
    setBusy(true);
    setBusyAction("enable");
    try {
      await invoke("enable_goyou", { lease: session.lease });
      await refresh();
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  };
  const disable = async () => {
    setBusy(true);
    setBusyAction("disable");
    try {
      await invoke("disable_goyou");
      await refresh();
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  };
  useEffect(() => {
    if (!usage?.exceeded) {
      autoClosedDate.current = null;
      return;
    }
    if (
      !status?.tunnelRunning ||
      busy ||
      autoClosedDate.current === usage.date
    ) {
      return;
    }
    autoClosedDate.current = usage.date;
    setMessage("今日流量额度已用尽，正在自动关闭代理");
    void disable()
      .then(() => setMessage("今日流量额度已用尽，代理已自动关闭"))
      .catch((error) =>
        setMessage(`今日流量额度已用尽，但自动关闭失败：${String(error)}`),
      );
  }, [busy, status?.tunnelRunning, usage]);
  const diagnose = async () => {
    setBusy(true);
    setBusyAction("network");
    try {
      const result = await invoke<Diagnostic>("diagnose_goyou");
      const reachability = `GitHub ${result.githubReachable ? "可达" : "不可达"}`;
      const gitProxyStatus = result.gitProxyMatchesTunnel
        ? "Git 已指向本地代理"
        : result.gitProxyConfigured
          ? "Git 使用其他代理"
          : "Git 未配置全局代理";
      setMessage(
        result.githubReachable
          ? `${reachability}；耗时 ${result.latencyMs} ms；${gitProxyStatus}`
          : `${reachability}；${result.error ?? "网络不可达"}`,
      );
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  };
  const setStartup = async (command: string, enabled: boolean) => {
    setBusy(true);
    try {
      await invoke(command, { enabled });
      await refresh();
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };
  const formatBytes = (bytes: number) => {
    if (bytes < 1000000) return `${bytes} B`;
    return `${(bytes / 1000000).toFixed(bytes >= 1000000000 ? 1 : 0)} MB`;
  };
  const confirmLogout = async () => {
    setBusy(true);
    setBusyAction("disable");
    try {
      await invoke("disable_goyou");
    } catch (error) {
      setMessage(`关闭代理返回提示，仍将退出登录：${String(error)}`);
    } finally {
      onLogout();
      setBusy(false);
      setBusyAction(null);
    }
  };
  const on = status?.state === "on";
  const stateLabel = !status
    ? "读取中"
    : on
      ? "代理已开启"
      : status.state === "error"
        ? "代理异常"
        : "代理已关闭";
  const stateTone = !status
    ? "pending"
    : on
      ? "running"
      : status.state === "error"
        ? "error"
        : "stopped";

  return (
    <main className="dashboard-shell">
      <section className="dashboard-card">
        <header>
          <div>
            <div className="dashboard-title-row">
              <div
                className="brand-mark dashboard-brand-mark"
                aria-hidden="true"
              >
                <span />
                <span />
                <span />
              </div>
              <h1>GoYou</h1>
              <span className={`overall-status ${stateTone}`}>
                <i />
                {stateLabel}
              </span>
            </div>
          </div>
          <div className="member-info">
            <div className="account-menu">
              <span
                aria-label={`${session.user.name}，${session.user.email}`}
                className="account-avatar logout-avatar"
                data-tooltip={`${session.user.name} · ${session.user.email}`}
                title={`${session.user.name} · ${session.user.email}`}
              >
                {session.user.name.slice(0, 1).toUpperCase()}
              </span>
              <button
                className="logout-button"
                onClick={() => setShowLogoutConfirm(true)}
                disabled={busy}
                type="button"
              >
                退出
              </button>
            </div>
            <p className="lease-summary">
              有效期至{" "}
              {new Date(session.lease.expires_at).toLocaleDateString("zh-CN", {
                year: "numeric",
                month: "long",
                day: "numeric",
              })}
            </p>
          </div>
        </header>
        <div className="dashboard-actions">
          <button
            className={`${on ? "danger" : "primary-action"} action-button ${busyAction === "enable" || busyAction === "disable" ? "is-loading" : ""}`}
            disabled={!status || busy}
            onClick={() => void (on ? disable() : enable())}
          >
            {(busyAction === "enable" || busyAction === "disable") && (
              <span className="button-spinner" />
            )}
            {busyAction === "enable" || busyAction === "disable"
              ? busyAction === "disable"
                ? "正在关闭…"
                : "正在开启…"
              : on
                ? "关闭代理"
                : "开启代理"}
          </button>
          <button
            className={`secondary action-button ${busyAction === "network" ? "is-loading" : ""}`}
            disabled={!status || busy}
            onClick={() => void diagnose()}
          >
            {busyAction === "network" && <span className="button-spinner" />}
            {busyAction === "network" ? "检测中…" : "检测网络"}
          </button>
        </div>
        <dl>
          <div>
            <dt>本地 SOCKS</dt>
            <dd>127.0.0.1:7890</dd>
          </div>
          <div>
            <dt>代理进程</dt>
            <dd
              className={
                status?.tunnelRunning ? "state running" : "state stopped"
              }
            >
              <i />
              {status?.tunnelRunning ? "运行中" : "未运行"}
            </dd>
          </div>
          <div>
            <dt>本地端口</dt>
            <dd
              className={
                status?.portListening ? "state running" : "state stopped"
              }
            >
              <i />
              {status?.portListening ? "正在监听" : "未监听"}
            </dd>
          </div>
          <div>
            <dt>系统代理</dt>
            <dd
              className={
                status?.systemProxyEnabled ? "state running" : "state stopped"
              }
            >
              <i />
              {status?.systemProxyEnabled ? "已启用" : "未启用"}
            </dd>
          </div>
        </dl>
        <div className="settings-row">
          <label>
            <input
              checked={autoLaunch}
              disabled={busy}
              onChange={(event) =>
                void setStartup("set_auto_launch", event.target.checked)
              }
              type="checkbox"
            />
            开机启动应用
          </label>
          <label>
            <input
              checked={status?.gitProxyEnabled ?? false}
              disabled={busy}
              onChange={(event) =>
                void setStartup("set_goyou_git_proxy", event.target.checked)
              }
              type="checkbox"
            />
            Git使用代理
          </label>
        </div>
        {usage && (
          <div className={`traffic-card ${usage.exceeded ? "exceeded" : ""}`}>
            <div className="traffic-card-header">
              <span>今日流量</span>
              <strong>
                {formatBytes(usage.used_bytes)} /{" "}
                {formatBytes(usage.quota_bytes)}
              </strong>
            </div>
            <div className="traffic-progress" aria-hidden="true">
              <span style={{ width: `${Math.min(usage.percentage, 100)}%` }} />
            </div>
            <small>
              {usage.exceeded
                ? "今日额度已用尽，代理已关闭"
                : `剩余 ${formatBytes(usage.remaining_bytes)}，${new Date(usage.resets_at).toLocaleString("zh-CN", { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" })} 重置`}
            </small>
          </div>
        )}
        <p className={`message ${status?.lastError ? "error" : ""}`}>
          {status?.lastError ?? message}
        </p>
        <small className="app-version">v{appPackage.version}</small>
      </section>
      {showLogoutConfirm && (
        <div className="modal-backdrop" role="presentation">
          <section
            aria-labelledby="logout-dialog-title"
            aria-modal="true"
            className="confirm-dialog"
            role="dialog"
          >
            <h2 id="logout-dialog-title">确认退出登录？</h2>
            <p>退出前会先关闭代理，并恢复相关网络设置。</p>
            <div className="confirm-actions">
              <button
                className="cancel-button"
                disabled={busy}
                onClick={() => setShowLogoutConfirm(false)}
                type="button"
              >
                取消
              </button>
              <button
                className="confirm-button action-button"
                disabled={busy}
                onClick={() => void confirmLogout()}
                type="button"
              >
                {busy && <span className="button-spinner" />}
                {busy ? "处理中…" : "确定"}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}

export default function App() {
  const [session, setSession] = useState<AuthSession | null>(() =>
    getSession(),
  );
  return session ? (
    <Dashboard
      session={session}
      onSessionRefreshed={setSession}
      onLogout={() => {
        void logout();
        setSession(null);
      }}
    />
  ) : (
    <AuthPage onAuthenticated={setSession} />
  );
}
