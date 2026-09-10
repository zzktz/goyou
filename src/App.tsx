import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import appPackage from "../package.json";
import {
  clearRememberedLogin,
  getSession,
  getRememberedLogin,
  getTodayUsage,
  login,
  logout,
  refreshSession,
  saveRememberedLogin,
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

function isAuthFailure(error: unknown): boolean {
  return error instanceof Error && /登录|令牌|401/.test(error.message);
}

function isAccountExpiredError(error: unknown): boolean {
  return error instanceof Error && /账户已到期/.test(error.message);
}

function isAccountExpired(expiryDate: string | null | undefined): boolean {
  if (!expiryDate) return false;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  })
    .formatToParts(new Date())
    .reduce<Record<string, string>>((values, part) => {
      if (part.type !== "literal") values[part.type] = part.value;
      return values;
    }, {});
  const today = `${parts.year}-${parts.month}-${parts.day}`;
  return expiryDate < today;
}

function formatAccountExpiry(expiryDate: string | null | undefined): string {
  if (!expiryDate) return "长期有效";
  return new Date(`${expiryDate}T12:00:00`).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function AuthPage({
  onAuthenticated,
}: {
  onAuthenticated: (session: AuthSession) => void;
}) {
  const rememberedLogin = getRememberedLogin();
  const [email, setEmail] = useState(rememberedLogin?.email ?? "");
  const [password, setPassword] = useState(rememberedLogin?.password ?? "");
  const [rememberLogin, setRememberLogin] = useState(rememberedLogin !== null);
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
      if (rememberLogin) {
        saveRememberedLogin(normalizedEmail, password);
      } else {
        clearRememberedLogin();
      }
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
            <label className="remember-option">
              <input
                checked={rememberLogin}
                onChange={(event) => setRememberLogin(event.target.checked)}
                type="checkbox"
              />
              <span>记住账号密码</span>
            </label>
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
  const [usageError, setUsageError] = useState<string | null>(null);
  const autoClosedDate = useRef<string | null>(null);
  const startupRefreshDone = useRef(false);
  const authFailureHandled = useRef(false);
  const accountExpiryHandled = useRef(false);
  const currentSession = useRef(session);
  const sessionRefreshInFlight = useRef<Promise<AuthSession> | null>(null);

  useEffect(() => {
    currentSession.current = session;
  }, [session]);

  const refreshAuthenticatedSession = useCallback(
    (activeSession: AuthSession = currentSession.current) => {
      if (sessionRefreshInFlight.current) {
        return sessionRefreshInFlight.current;
      }
      const request = refreshSession(activeSession)
        .then((nextSession) => {
          currentSession.current = nextSession;
          return nextSession;
        })
        .finally(() => {
          if (sessionRefreshInFlight.current === request) {
            sessionRefreshInFlight.current = null;
          }
        });
      sessionRefreshInFlight.current = request;
      return request;
    },
    [],
  );

  const handleAuthFailure = useCallback(async () => {
    if (authFailureHandled.current) return;
    authFailureHandled.current = true;
    await invoke("disable_goyou").catch(() => undefined);
    onLogout();
  }, [onLogout]);

  const handleAccountExpired = useCallback(async () => {
    if (!accountExpiryHandled.current) {
      accountExpiryHandled.current = true;
      await invoke("disable_goyou").catch(() => undefined);
      await invoke<Status>("get_goyou_status")
        .then(setStatus)
        .catch(() => undefined);
    }
    setMessage("您的账户已到期，不可继续使用代理。");
  }, []);

  const refresh = useCallback(
    async (activeSession: AuthSession = session) => {
      const [next, launch] = await Promise.all([
        invoke<Status>("get_goyou_status"),
        invoke<boolean>("get_auto_launch_status"),
      ]);
      setStatus(next);
      setAutoLaunch(launch);
      if (isAccountExpired(activeSession.user.account_expires_at)) {
        await handleAccountExpired();
      }
      try {
        const latestUsage = await getTodayUsage(activeSession);
        setUsage(latestUsage);
        setUsageError(null);
        if (latestUsage.account_expired) {
          await handleAccountExpired();
        }
      } catch (error) {
        if (isAuthFailure(error)) {
          try {
            const renewedSession =
              await refreshAuthenticatedSession(activeSession);
            onSessionRefreshed(renewedSession);
            if (isAccountExpired(renewedSession.user.account_expires_at)) {
              await handleAccountExpired();
            }
            const latestUsage = await getTodayUsage(renewedSession);
            setUsage(latestUsage);
            setUsageError(null);
            if (latestUsage.account_expired) {
              await handleAccountExpired();
            }
            return;
          } catch (renewalError) {
            setUsage(null);
            setUsageError("今日流量暂不可用，请重新登录后查看");
            if (isAccountExpiredError(renewalError)) {
              await handleAccountExpired();
            } else if (isAuthFailure(renewalError)) {
              void handleAuthFailure();
            }
            return;
          }
        }
        setUsage(null);
        setUsageError("今日流量暂时不可用");
      }
    },
    [
      handleAccountExpired,
      handleAuthFailure,
      onSessionRefreshed,
      refreshAuthenticatedSession,
      session,
    ],
  );

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (startupRefreshDone.current) return;
    startupRefreshDone.current = true;
    let cancelled = false;
    void invoke<Status>("get_goyou_status")
      .then(async (nextStatus) => {
        if (nextStatus.systemProxyEnabled && !nextStatus.tunnelRunning) {
          await invoke("disable_goyou");
        }
        return refreshAuthenticatedSession();
      })
      .then(async (nextSession) => {
        if (cancelled) return;
        onSessionRefreshed(nextSession);
        if (isAccountExpired(nextSession.user.account_expires_at)) {
          await handleAccountExpired();
        }
        return refresh(nextSession);
      })
      .catch((error) => {
        if (isAccountExpiredError(error)) void handleAccountExpired();
        else if (isAuthFailure(error)) void handleAuthFailure();
      });
    return () => {
      cancelled = true;
    };
  }, [
    handleAccountExpired,
    handleAuthFailure,
    onSessionRefreshed,
    refresh,
    refreshAuthenticatedSession,
  ]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshAuthenticatedSession()
        .then((nextSession) => {
          onSessionRefreshed(nextSession);
          if (isAccountExpired(nextSession.user.account_expires_at)) {
            return handleAccountExpired();
          }
          setMessage("登录令牌和代理租约已刷新");
        })
        .catch((error) => {
          if (isAccountExpiredError(error)) {
            void handleAccountExpired();
          } else if (isAuthFailure(error)) {
            void handleAuthFailure();
          } else {
            setMessage("登录状态暂时无法刷新，请稍后重试");
          }
        });
    }, 10 * 60_000);
    return () => window.clearInterval(timer);
  }, [
    handleAccountExpired,
    handleAuthFailure,
    onSessionRefreshed,
    refreshAuthenticatedSession,
  ]);

  useEffect(() => {
    if (isAccountExpired(session.user.account_expires_at)) {
      void handleAccountExpired();
    } else {
      accountExpiryHandled.current = false;
    }
  }, [handleAccountExpired, session.user.account_expires_at]);

  const enable = async () => {
    if (!status) return;
    if (isAccountExpired(session.user.account_expires_at)) {
      await handleAccountExpired();
      return;
    }
    if (usage?.exceeded) {
      setMessage("今日流量额度已用尽，代理将在明日 00:00 后恢复");
      return;
    }
    setBusy(true);
    setBusyAction("enable");
    setMessage("正在检查登录状态和流量额度…");
    try {
      const activeSession = await refreshAuthenticatedSession();
      onSessionRefreshed(activeSession);
      if (isAccountExpired(activeSession.user.account_expires_at)) {
        await handleAccountExpired();
        return;
      }
      if (!activeSession.lease) {
        setMessage("代理租约暂不可用，请稍后重试");
        return;
      }
      const latestUsage = await getTodayUsage(activeSession);
      setUsage(latestUsage);
      setUsageError(null);
      if (latestUsage.account_expired) {
        await handleAccountExpired();
        return;
      }
      if (latestUsage.exceeded) {
        setMessage("今日流量额度已用尽，代理将在明日 00:00 后恢复");
        return;
      }
      await invoke("enable_goyou", { lease: activeSession.lease });
      await refresh(activeSession);
      setMessage("代理已开启");
    } catch (error) {
      if (isAccountExpiredError(error)) {
        await handleAccountExpired();
      } else if (isAuthFailure(error)) {
        await handleAuthFailure();
      } else {
        setMessage(String(error));
      }
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  };
  const disable = async () => {
    setBusy(true);
    setBusyAction("disable");
    setMessage("正在关闭代理…");
    // Let React paint the loading state before the native command starts. On
    // Windows the IPC call can otherwise occupy the current frame, making the
    // button look unresponsive until the proxy has already stopped.
    await new Promise<void>((resolve) =>
      window.requestAnimationFrame(() => resolve()),
    );
    try {
      await invoke("disable_goyou");
      await refresh();
      setMessage("代理已关闭");
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
      const diagnosticError = result.error?.includes("proxy")
        ? "代理上游节点不可达，请稍后重试"
        : result.error;
      setMessage(
        result.githubReachable
          ? `${reachability}；耗时 ${result.latencyMs} ms；${gitProxyStatus}`
          : `${reachability}；${diagnosticError ?? "网络不可达"}`,
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
    const megabytes = bytes / 1000000;
    const precision =
      megabytes < 1 ? 2 : megabytes < 10 || megabytes >= 1000 ? 1 : 0;
    return `${megabytes.toFixed(precision)} MB`;
  };
  const usagePercentage = usage
    ? Math.max(0, Math.min(usage.percentage, 100))
    : 0;
  const usageWarning =
    usage !== null && !usage.exceeded && usage.percentage >= 80;
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
  const displayedMessage =
    message === "尚未检测网络连通性" ? (status?.lastError ?? message) : message;
  const proxyStatusTone = !status
    ? "pending"
    : status.state === "error" || status.lastError
      ? "error"
      : status.state === "on"
        ? "running"
        : "stopped";
  const proxyStatusLabel =
    proxyStatusTone === "pending"
      ? "读取中"
      : proxyStatusTone === "running"
        ? "正常"
        : proxyStatusTone === "error"
          ? "异常"
          : "未开启";
  const detailStatusTone = (active: boolean) =>
    active
      ? "is-active"
      : status?.state === "error" || status?.lastError
        ? "is-error"
        : "is-inactive";

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
              <span className="brand-version dashboard-version">
                v{appPackage.version}
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
              有效期至 {formatAccountExpiry(session.user.account_expires_at)}
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
          <div
            aria-live="polite"
            className={`traffic-card traffic-grid-card ${usage?.exceeded ? "exceeded" : usageWarning ? "warning" : ""}`}
            role="status"
          >
            <div className="traffic-card-header">
              <span>今日流量</span>
              <strong>
                {usage
                  ? `${formatBytes(usage.used_bytes)} / ${formatBytes(usage.quota_bytes)}`
                  : "读取中"}
              </strong>
            </div>
            <div className="traffic-progress" aria-hidden="true">
              <span style={{ width: `${usagePercentage}%` }} />
            </div>
            <small className={usageWarning ? "traffic-alert" : undefined}>
              {usage?.exceeded
                ? "今日额度已用尽，代理已关闭"
                : usageWarning
                  ? `已使用 ${usage.percentage.toFixed(0)}%，请注意流量；剩余 ${formatBytes(usage.remaining_bytes)}，${new Date(usage.resets_at).toLocaleString("zh-CN", { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" })} 重置`
                  : usage
                    ? `剩余 ${formatBytes(usage.remaining_bytes)}，${new Date(usage.resets_at).toLocaleString("zh-CN", { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" })} 重置`
                    : (usageError ?? "正在读取今日流量")}
            </small>
          </div>
          <div className="proxy-status-card">
            <div className="proxy-status-heading">
              <div className="proxy-status-title">代理状态</div>
              <span
                aria-label={`代理状态：${proxyStatusLabel}`}
                className={`proxy-status-indicator ${proxyStatusTone}`}
                data-tooltip={`代理状态：${proxyStatusLabel}`}
                role="img"
                tabIndex={0}
              >
                <i />
                {proxyStatusLabel}
              </span>
            </div>
            {(status?.state === "on" || status?.state === "error") && (
              <div className="proxy-status-items">
                <div className="proxy-status-item">
                  <span className="proxy-status-label">代理进程:</span>
                  <span
                    className={`proxy-status-value ${detailStatusTone(status.tunnelRunning)}`}
                  >
                    {status.tunnelRunning ? "运行中" : "未运行"}
                  </span>
                </div>
                <div className="proxy-status-item">
                  <span className="proxy-status-label">本地端口:</span>
                  <span
                    className={`proxy-status-value ${detailStatusTone(status.portListening)}`}
                  >
                    {status.portListening ? "正在监听" : "未监听"}
                  </span>
                </div>
                <div className="proxy-status-item">
                  <span className="proxy-status-label">系统代理:</span>
                  <span
                    className={`proxy-status-value ${detailStatusTone(status.systemProxyEnabled)}`}
                  >
                    {status.systemProxyEnabled ? "已启用" : "未启用"}
                  </span>
                </div>
              </div>
            )}
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
        <p className={`message ${status?.lastError ? "error" : ""}`}>
          {displayedMessage}
        </p>
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
