import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { relaunch } from "@tauri-apps/plugin-process";
import {
  check,
  type DownloadEvent,
  type Update,
} from "@tauri-apps/plugin-updater";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import appPackage from "../package.json";
import {
  clearRememberedLogin,
  createFeedback,
  getAuthSettings,
  getFeedback,
  getSession,
  getRememberedLogin,
  getTodayUsage,
  login,
  logout,
  register as registerAccount,
  requestPasswordResetCode,
  requestRegistrationCode,
  refreshSession,
  resetPassword,
  saveRememberedLogin,
  formatErrorMessage,
  updateProfile,
} from "./auth";
import type { AuthSession, FeedbackItem, UsageSummary } from "./auth";

interface Status {
  state: "on" | "off" | "error";
  tunnelRunning: boolean;
  localPort: number;
  portListening: boolean;
  systemProxyEnabled: boolean;
  proxyEnabled: boolean;
  gitProxyEnabled: boolean;
  lastError: string | null;
  proxyMode: "sing-box" | "ssh" | null;
}

interface Diagnostic {
  proxyRunning: boolean;
  localProxyReachable: boolean;
  upstreamReachable: boolean | null;
  failureKind: "local_proxy" | "upstream" | "auth" | "target" | null;
  upstreamError: string | null;
  githubReachable: boolean;
  latencyMs: number;
  githubStatus: number | null;
  googleReachable: boolean;
  googleLatencyMs: number;
  googleStatus: number | null;
  gitProxyConfigured: boolean;
  gitProxyMatchesTunnel: boolean;
  systemProxyEnabled: boolean;
  proxyMode: "sing-box" | "ssh" | null;
  analysis: string;
  error: string | null;
}

type MessageTone = "normal" | "warning";

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
  const [mode, setMode] = useState<"login" | "register" | "forgot">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState(rememberedLogin?.email ?? "");
  const [password, setPassword] = useState(rememberedLogin?.password ?? "");
  const [showPassword, setShowPassword] = useState(false);
  const [verificationCode, setVerificationCode] = useState("");
  const [codeBusy, setCodeBusy] = useState(false);
  const [codeCountdown, setCodeCountdown] = useState(0);
  const [rememberLogin, setRememberLogin] = useState(rememberedLogin !== null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);
  const [registrationEnabled, setRegistrationEnabled] = useState(true);

  useEffect(() => {
    let active = true;
    void getAuthSettings()
      .then((settings) => {
        if (active) setRegistrationEnabled(settings.registration_enabled);
      })
      .catch(() => {
        // Keep registration available if an older API does not expose settings.
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (codeCountdown <= 0) return;
    const timer = window.setInterval(() => {
      setCodeCountdown((current) => Math.max(current - 1, 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [codeCountdown]);

  const switchMode = (nextMode: "login" | "register" | "forgot") => {
    if (nextMode === "register" && !registrationEnabled) {
      setError("当前暂未开放注册");
      return;
    }
    setMode(nextMode);
    setError("");
    setSuccess("");
    setCodeCountdown(0);
    setVerificationCode("");
  };

  const openForgotPassword = () => {
    if (!email.trim()) {
      setError("请先输入邮箱，再点击忘记密码");
      return;
    }
    switchMode("forgot");
  };

  const sendVerificationCode = async () => {
    const normalizedEmail = email.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail)) {
      setError("请输入有效的邮箱地址");
      return;
    }
    setCodeBusy(true);
    setError("");
    try {
      if (mode === "forgot") {
        await requestPasswordResetCode(normalizedEmail);
      } else {
        await requestRegistrationCode(normalizedEmail);
      }
      setCodeCountdown(60);
    } catch (submissionError) {
      setError(formatErrorMessage(submissionError));
    } finally {
      setCodeBusy(false);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setSuccess("");
    const normalizedEmail = email.trim();
    if (!normalizedEmail || !normalizedEmail.includes("@")) {
      setError("请输入有效的邮箱地址");
      return;
    }
    if (password.length < 8) {
      setError("密码至少需要 8 位");
      return;
    }
    if (
      mode === "register" &&
      name &&
      !/^[A-Za-z\u4e00-\u9fff]{1,10}$/.test(name)
    ) {
      setError("姓名只能包含中文或英文字母，最多 10 个字符");
      return;
    }
    if (
      (mode === "register" || mode === "forgot") &&
      !/^\d{6}$/.test(verificationCode)
    ) {
      setError("请输入 6 位邮箱验证码");
      return;
    }
    if (mode === "forgot") {
      setBusy(true);
      try {
        await resetPassword(normalizedEmail, verificationCode, password);
        setMode("login");
        setPassword("");
        setVerificationCode("");
        setCodeCountdown(0);
        setSuccess("密码已重置，请使用新密码登录");
      } catch (submissionError) {
        setError(formatErrorMessage(submissionError));
      } finally {
        setBusy(false);
      }
      return;
    }
    setBusy(true);
    try {
      const session =
        mode === "register"
          ? await registerAccount(
              name,
              normalizedEmail,
              verificationCode,
              password,
            )
          : await login(normalizedEmail, password);
      if (rememberLogin) {
        saveRememberedLogin(normalizedEmail, password);
      } else {
        clearRememberedLogin();
      }
      onAuthenticated(session);
    } catch (submissionError) {
      setError(formatErrorMessage(submissionError));
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
          <h1 id="auth-title">
            {mode === "login"
              ? "用户登录"
              : mode === "register"
                ? "注册账号"
                : "重置密码"}
          </h1>
        </div>
        <form className="auth-form" onSubmit={(event) => void submit(event)}>
          {mode === "register" && (
            <div className="auth-field">
              <label htmlFor="register-name">姓名</label>
              <input
                id="register-name"
                autoComplete="name"
                maxLength={10}
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="中文或英文，最多 10 个字符"
              />
            </div>
          )}
          <div className="auth-field">
            <label htmlFor="login-email">邮箱</label>
            <input
              id="login-email"
              autoComplete="email"
              inputMode="email"
              readOnly={mode === "forgot"}
              required
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
            />
          </div>
          {(mode === "register" || mode === "forgot") && (
            <div className="auth-field auth-code-field">
              <label htmlFor="verification-code">验证码</label>
              <div className="auth-code-input">
                <input
                  id="verification-code"
                  inputMode="numeric"
                  maxLength={6}
                  value={verificationCode}
                  onChange={(event) =>
                    setVerificationCode(event.target.value.replace(/\D/g, ""))
                  }
                  placeholder="6 位验证码"
                />
                <button
                  className="code-button"
                  disabled={codeBusy || codeCountdown > 0}
                  onClick={() => void sendVerificationCode()}
                  type="button"
                >
                  {codeBusy
                    ? "发送中"
                    : codeCountdown > 0
                      ? `${codeCountdown}s`
                      : "获取验证码"}
                </button>
              </div>
            </div>
          )}
          <div className="auth-field">
            <label htmlFor="login-password">
              {mode === "forgot" ? "新密码" : "密码"}
            </label>
            <div className="password-input-wrap">
              <input
                id="login-password"
                autoComplete={
                  mode === "login" ? "current-password" : "new-password"
                }
                required
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="至少 8 位字符"
              />
              <button
                aria-label={showPassword ? "隐藏密码" : "显示密码"}
                className="password-toggle"
                onClick={() => setShowPassword((visible) => !visible)}
                title={showPassword ? "隐藏密码" : "显示密码"}
                type="button"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24">
                  {showPassword ? (
                    <>
                      <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
                      <circle cx="12" cy="12" r="2.5" />
                    </>
                  ) : (
                    <>
                      <path d="m3 3 18 18" />
                      <path d="M10.6 6.2A10.7 10.7 0 0 1 12 6c6 0 9.5 6 9.5 6a17 17 0 0 1-3.1 3.6M6.5 6.8C4 8.2 2.5 12 2.5 12s3.5 6 9.5 6c1.1 0 2.1-.2 3-.5" />
                      <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
                    </>
                  )}
                </svg>
              </button>
            </div>
          </div>
          {mode === "login" && (
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
                onClick={openForgotPassword}
              >
                忘记密码？
              </button>
            </div>
          )}
          {error && (
            <p className="auth-error" role="alert">
              {error}
            </p>
          )}
          {success && <p className="auth-success">{success}</p>}
          <button className="submit-button" disabled={busy} type="submit">
            {busy
              ? "处理中…"
              : mode === "login"
                ? "登录"
                : mode === "register"
                  ? "注册并登录"
                  : "重置密码"}
          </button>
        </form>
        {mode === "login" && !registrationEnabled ? (
          <p className="auth-mode-button">注册功能暂未开放</p>
        ) : (
          <button
            className="auth-mode-button"
            onClick={() => switchMode(mode === "login" ? "register" : "login")}
            type="button"
          >
            {mode === "login" ? "没有账号？注册" : "返回登录"}
          </button>
        )}
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
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const [profileName, setProfileName] = useState(session.user.name);
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [showFeedback, setShowFeedback] = useState(false);
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackFiles, setFeedbackFiles] = useState<File[]>([]);
  const [feedbackItems, setFeedbackItems] = useState<FeedbackItem[]>([]);
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [feedbackError, setFeedbackError] = useState<string | null>(null);
  const [diagnosticResult, setDiagnosticResult] = useState<Diagnostic | null>(
    null,
  );
  const [diagnosticConsentOpen, setDiagnosticConsentOpen] = useState(false);
  const [diagnosticSubmitting, setDiagnosticSubmitting] = useState(false);
  const [message, setMessage] = useState("尚未检测网络连通性");
  const [messageTone, setMessageTone] = useState<MessageTone>("normal");
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [availableUpdate, setAvailableUpdate] = useState<Update | null>(null);
  const [hasAvailableUpdate, setHasAvailableUpdate] = useState(false);
  const [updateDialogOpen, setUpdateDialogOpen] = useState(false);
  const [updateState, setUpdateState] = useState<
    | "idle"
    | "checking"
    | "available"
    | "downloading"
    | "installing"
    | "latest"
    | "error"
  >("idle");
  const [updateProgress, setUpdateProgress] = useState(0);
  const [updateError, setUpdateError] = useState<string | null>(null);
  const autoClosedDate = useRef<string | null>(null);
  const startupRefreshDone = useRef(false);
  const authFailureHandled = useRef(false);
  const accountExpiryHandled = useRef(false);
  const currentSession = useRef(session);
  const currentStatus = useRef<Status | null>(null);
  const sessionRefreshInFlight = useRef<Promise<AuthSession> | null>(null);
  const updateCheckInFlight = useRef(false);
  const accountMenuRef = useRef<HTMLDivElement>(null);

  const setInfoMessage = useCallback(
    (nextMessage: string, tone: MessageTone = "normal") => {
      setMessage(formatErrorMessage(nextMessage));
      setMessageTone(tone);
    },
    [],
  );

  const loadFeedback = useCallback(async () => {
    try {
      setFeedbackItems(await getFeedback(currentSession.current));
    } catch (error) {
      setFeedbackError(formatErrorMessage(error));
    }
  }, []);

  const openFeedback = () => {
    setAccountMenuOpen(false);
    setFeedbackError(null);
    setShowFeedback(true);
    void loadFeedback();
  };

  const openProfile = () => {
    setAccountMenuOpen(false);
    setProfileName(currentSession.current.user.name);
    setProfileError(null);
    setShowProfile(true);
  };

  const submitProfile = async () => {
    const name = profileName.trim();
    if (!name) {
      setProfileError("请输入名称。");
      return;
    }
    setProfileBusy(true);
    setProfileError(null);
    try {
      const nextSession = await updateProfile(currentSession.current, name);
      currentSession.current = nextSession;
      onSessionRefreshed(nextSession);
      setShowProfile(false);
      setInfoMessage("个人信息已保存。");
    } catch (error) {
      setProfileError(formatErrorMessage(error));
    } finally {
      setProfileBusy(false);
    }
  };

  const selectFeedbackScreenshots = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length) return;
    const accepted = files.filter(
      (file) =>
        ["image/png", "image/jpeg", "image/webp"].includes(file.type) &&
        file.size <= 5 * 1024 * 1024,
    );
    if (accepted.length !== files.length) {
      setFeedbackError("仅支持 PNG、JPEG、WebP 图片，单张不能超过 5 MiB。");
    }
    setFeedbackFiles((current) => {
      const next = [...current, ...accepted].slice(0, 3);
      if (current.length + accepted.length > 3) {
        setFeedbackError("最多可添加 3 张截图。");
      }
      return next;
    });
  };

  const submitFeedback = async () => {
    const message = feedbackText.trim();
    if (!message) {
      setFeedbackError("请描述你遇到的问题。");
      return;
    }
    setFeedbackBusy(true);
    setFeedbackError(null);
    try {
      const screenshots = await Promise.all(
        feedbackFiles.map(
          (file) =>
            new Promise<{ filename: string; data: string }>(
              (resolve, reject) => {
                const reader = new FileReader();
                reader.onerror = () =>
                  reject(new Error(`无法读取截图：${file.name}`));
                reader.onload = () => {
                  const dataUrl = String(reader.result ?? "");
                  const [, data = ""] = dataUrl.split(",", 2);
                  resolve({ filename: file.name, data });
                };
                reader.readAsDataURL(file);
              },
            ),
        ),
      );
      await createFeedback(currentSession.current, message, screenshots);
      setFeedbackText("");
      setFeedbackFiles([]);
      await loadFeedback();
      setInfoMessage("问题反馈已提交，我们会尽快回复。");
    } catch (error) {
      setFeedbackError(formatErrorMessage(error));
    } finally {
      setFeedbackBusy(false);
    }
  };

  const closeDiagnosticConsent = () => {
    setDiagnosticConsentOpen(false);
    setDiagnosticResult(null);
  };

  const submitDiagnosticReport = async () => {
    if (!diagnosticResult) return;
    setDiagnosticSubmitting(true);
    try {
      const result = diagnosticResult;
      const report = [
        "【自动网络问题收集】",
        "本地代理端口：" + (result.localProxyReachable ? "可连接" : "不可连接"),
        "代理上游节点：" +
          (result.upstreamReachable === null
            ? "未检测"
            : result.upstreamReachable
              ? "可连接"
              : "不可连接"),
        "故障类型：" +
          ({
            local_proxy: "本地端口未监听",
            upstream: "上游端口不可达",
            auth: "代理认证失败",
            target: "目标站点或 DNS 不可达",
          }[result.failureKind ?? "target"] ?? "目标站点或 DNS 不可达"),
        `应用版本：v${appPackage.version}`,
        `收集时间：${new Date().toLocaleString("zh-CN", { hour12: false })}`,
        `代理运行状态：${result.proxyRunning ? "运行中" : "未运行"}`,
        `GitHub：${result.githubReachable ? `可达（${result.latencyMs} ms，HTTP ${result.githubStatus ?? "?"}）` : "不可达"}`,
        `Google：${result.googleReachable ? `可达（${result.googleLatencyMs} ms，HTTP ${result.googleStatus ?? "?"}）` : "不可达"}`,
        `Git 代理：${result.gitProxyMatchesTunnel ? "已指向本地代理" : result.gitProxyConfigured ? "使用其他代理" : "未配置"}`,
        `系统代理：${result.systemProxyEnabled ? "已启用" : "未启用"}`,
        `代理模式：${result.proxyMode ?? "无"}`,
        `原因分析：${result.analysis}`,
        `错误信息：${result.error ?? "无"}`,
        `客户端环境：${navigator.userAgent}`,
      ].join("\n");
      await createFeedback(currentSession.current, report, []);
      closeDiagnosticConsent();
      setInfoMessage("问题检测结果已上传，感谢你的协助。");
    } catch (error) {
      setInfoMessage(
        `问题检测结果上传失败：${formatErrorMessage(error)}`,
        "warning",
      );
    } finally {
      setDiagnosticSubmitting(false);
    }
  };

  useEffect(() => {
    currentSession.current = session;
  }, [session]);

  useEffect(() => {
    currentStatus.current = status;
  }, [status]);

  useEffect(() => {
    if (!accountMenuOpen) return;
    const closeOnOutsidePress = (event: PointerEvent) => {
      if (!accountMenuRef.current?.contains(event.target as Node)) {
        setAccountMenuOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setAccountMenuOpen(false);
    };
    window.addEventListener("pointerdown", closeOnOutsidePress);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", closeOnOutsidePress);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [accountMenuOpen]);

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
    setInfoMessage("您的账户已到期，不可继续使用代理。", "warning");
  }, [setInfoMessage]);

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
          setInfoMessage("登录令牌和代理租约已刷新");
        })
        .catch((error) => {
          if (isAccountExpiredError(error)) {
            void handleAccountExpired();
          } else if (isAuthFailure(error)) {
            void handleAuthFailure();
          } else {
            setInfoMessage("登录状态暂时无法刷新，请稍后重试", "warning");
          }
        });
    }, 10 * 60_000);
    return () => window.clearInterval(timer);
  }, [
    handleAccountExpired,
    handleAuthFailure,
    onSessionRefreshed,
    refreshAuthenticatedSession,
    setInfoMessage,
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
      setInfoMessage(
        "今日流量额度已用尽，代理将在明日 00:00 后恢复",
        "warning",
      );
      return;
    }
    setBusy(true);
    setBusyAction("enable");
    setInfoMessage("正在检查登录状态和流量额度…");
    try {
      const activeSession = await refreshAuthenticatedSession();
      onSessionRefreshed(activeSession);
      if (isAccountExpired(activeSession.user.account_expires_at)) {
        await handleAccountExpired();
        return;
      }
      if (!activeSession.lease) {
        setInfoMessage("代理租约暂不可用，请稍后重试", "warning");
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
        setInfoMessage(
          "今日流量额度已用尽，代理将在明日 00:00 后恢复",
          "warning",
        );
        return;
      }
      await invoke("enable_goyou", { lease: activeSession.lease });
      await refresh(activeSession);
      setInfoMessage("代理已开启");
    } catch (error) {
      if (isAccountExpiredError(error)) {
        await handleAccountExpired();
      } else if (isAuthFailure(error)) {
        await handleAuthFailure();
      } else {
        setInfoMessage(formatErrorMessage(error), "warning");
      }
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  };
  const disable = async (tone: MessageTone = "normal") => {
    setBusy(true);
    setBusyAction("disable");
    setInfoMessage("正在关闭代理…", tone);
    // Let React paint the loading state before the native command starts. On
    // Windows the IPC call can otherwise occupy the current frame, making the
    // button look unresponsive until the proxy has already stopped.
    await new Promise<void>((resolve) =>
      window.requestAnimationFrame(() => resolve()),
    );
    try {
      await invoke("disable_goyou");
      await refresh();
      setInfoMessage("代理已关闭");
    } catch (error) {
      setInfoMessage(formatErrorMessage(error), "warning");
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  };
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void listen("tray:toggle-proxy", () => {
      if (busy) return;
      if (currentStatus.current?.state === "on") {
        void disable();
      } else {
        void enable();
      }
    }).then((stopListening) => {
      unlisten = stopListening;
    });
    return () => unlisten?.();
  }, [busy, disable, enable]);
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
    setInfoMessage("今日流量额度已用尽，正在自动关闭代理", "warning");
    void disable("warning")
      .then(() =>
        setInfoMessage("今日流量额度已用尽，代理已自动关闭", "warning"),
      )
      .catch((error) =>
        setInfoMessage(
          `今日流量额度已用尽，但自动关闭失败：${formatErrorMessage(error)}`,
          "warning",
        ),
      );
  }, [busy, status?.tunnelRunning, usage]);
  const diagnose = async () => {
    setBusy(true);
    setBusyAction("network");
    try {
      const result = await invoke<Diagnostic>("diagnose_goyou");
      const reachability = [
        `GitHub ${result.githubReachable ? "可达" : "不可达"}${result.githubReachable ? `（${result.latencyMs} ms）` : ""}`,
        `Google ${result.googleReachable ? "可达" : "不可达"}${result.googleReachable ? `（${result.googleLatencyMs} ms）` : ""}`,
      ].join("；");
      const gitProxyStatus = result.gitProxyMatchesTunnel
        ? "Git 已指向本地代理"
        : result.gitProxyConfigured
          ? "Git 使用其他代理"
          : "Git 未配置全局代理";
      const failureReason = result.failureKind
        ? {
            local_proxy: "本地代理端口不可用，请重新启动代理",
            upstream: "代理上游节点不可达，请稍后重试",
            auth: "代理认证失败，请刷新租约或重新登录",
            target: "目标站点或 DNS 不可达，请检查目标域名和线路",
          }[result.failureKind]
        : null;
      const diagnosticError = failureReason || result.error;
      setInfoMessage(
        result.githubReachable && result.googleReachable
          ? `${reachability}；${gitProxyStatus}`
          : `${reachability}；${result.analysis}${diagnosticError ? `；${diagnosticError}` : ""}`,
        result.githubReachable && result.googleReachable ? "normal" : "warning",
      );
      if (
        result.proxyRunning &&
        (!result.githubReachable || !result.googleReachable)
      ) {
        setDiagnosticResult(result);
        setDiagnosticConsentOpen(true);
      } else {
        setDiagnosticResult(null);
        setDiagnosticConsentOpen(false);
      }
    } catch (error) {
      setInfoMessage(formatErrorMessage(error), "warning");
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
      setInfoMessage(formatErrorMessage(error), "warning");
    } finally {
      setBusy(false);
    }
  };
  const checkForUpdates = async () => {
    if (
      updateState === "checking" ||
      updateState === "downloading" ||
      updateState === "installing" ||
      updateCheckInFlight.current
    ) {
      return;
    }
    updateCheckInFlight.current = true;
    setUpdateDialogOpen(true);
    setUpdateState("checking");
    setUpdateProgress(0);
    setUpdateError(null);
    await availableUpdate?.close().catch(() => undefined);
    setAvailableUpdate(null);
    try {
      const nextUpdate = await check({
        timeout: 15_000,
        ...(currentStatus.current?.tunnelRunning
          ? { proxy: "http://127.0.0.1:7890" }
          : {}),
      });
      if (!nextUpdate) {
        setAvailableUpdate(null);
        setHasAvailableUpdate(false);
        setUpdateState("latest");
        return;
      }
      setAvailableUpdate(nextUpdate);
      setHasAvailableUpdate(true);
      setUpdateState("available");
    } catch (error) {
      setUpdateState("error");
      setUpdateError(formatErrorMessage(error));
    } finally {
      updateCheckInFlight.current = false;
    }
  };
  const closeUpdateDialog = async () => {
    if (updateState === "downloading" || updateState === "installing") return;
    await availableUpdate?.close().catch(() => undefined);
    setAvailableUpdate(null);
    setUpdateDialogOpen(false);
    setUpdateState("idle");
    setUpdateError(null);
  };
  useEffect(() => {
    let cancelled = false;
    const checkForAvailableUpdate = async () => {
      if (updateCheckInFlight.current) return;
      updateCheckInFlight.current = true;
      try {
        const update = await check({
          timeout: 15_000,
          ...(currentStatus.current?.tunnelRunning
            ? { proxy: "http://127.0.0.1:7890" }
            : {}),
        });
        if (!cancelled) setHasAvailableUpdate(Boolean(update));
        await update?.close().catch(() => undefined);
      } catch {
        // Keep an existing update indicator visible when a transient check fails.
      } finally {
        updateCheckInFlight.current = false;
      }
    };
    const checkWhenFocused = () => void checkForAvailableUpdate();

    void checkForAvailableUpdate();
    const interval = window.setInterval(
      () => void checkForAvailableUpdate(),
      5 * 60_000,
    );
    window.addEventListener("focus", checkWhenFocused);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      window.removeEventListener("focus", checkWhenFocused);
    };
  }, []);
  const installUpdate = async () => {
    if (!availableUpdate) return;
    setBusy(true);
    setUpdateState("downloading");
    setUpdateProgress(0);
    setUpdateError(null);
    try {
      await invoke("disable_goyou");
      let downloaded = 0;
      let total = 0;
      await availableUpdate.downloadAndInstall((event: DownloadEvent) => {
        if (event.event === "Started") {
          total = event.data.contentLength ?? 0;
        } else if (event.event === "Progress") {
          downloaded += event.data.chunkLength;
          if (total > 0)
            setUpdateProgress(Math.min(100, (downloaded / total) * 100));
        } else if (event.event === "Finished") {
          setUpdateProgress(100);
        }
      });
      setUpdateState("installing");
      // Windows exits from downloadAndInstall after starting its installer.
      // macOS needs an explicit relaunch to load the newly installed bundle.
      if (/Macintosh|Mac OS X/.test(navigator.userAgent)) {
        await relaunch();
      }
    } catch (error) {
      setBusy(false);
      setUpdateState("error");
      setUpdateError(formatErrorMessage(error));
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
      setInfoMessage(
        `关闭代理返回提示，仍将退出登录：${formatErrorMessage(error)}`,
        "warning",
      );
    } finally {
      onLogout();
      setBusy(false);
      setBusyAction(null);
    }
  };
  const on = status?.state === "on";
  const displayedMessage = formatErrorMessage(
    message === "尚未检测网络连通性" ? (status?.lastError ?? message) : message,
  );
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
              <button
                className="brand-version dashboard-version brand-version-button"
                onClick={() => void checkForUpdates()}
                title="检查更新"
                type="button"
              >
                v{appPackage.version}
                {hasAvailableUpdate && (
                  <span
                    aria-label="有新版本可更新"
                    className="update-available-dot"
                    role="img"
                  />
                )}
              </button>
            </div>
          </div>
          <div className="member-info">
            <div className="account-menu" ref={accountMenuRef}>
              <span
                aria-hidden="true"
                className="account-avatar virtual-avatar"
              >
                <i />
                <b />
              </span>
              <button
                aria-expanded={accountMenuOpen}
                aria-haspopup="menu"
                className="account-name-button"
                onClick={() => setAccountMenuOpen((open) => !open)}
                type="button"
              >
                {session.user.name}
                <span aria-hidden="true" className="account-menu-chevron" />
              </button>
              {accountMenuOpen && (
                <div
                  aria-label="账户菜单"
                  className="account-dropdown"
                  role="menu"
                >
                  <button onClick={openProfile} role="menuitem" type="button">
                    个人信息
                  </button>
                  <button onClick={openFeedback} role="menuitem" type="button">
                    问题反馈
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => {
                      setAccountMenuOpen(false);
                      setShowLogoutConfirm(true);
                    }}
                    role="menuitem"
                    type="button"
                  >
                    退出登录
                  </button>
                </div>
              )}
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
                    {status.portListening ? status.localPort : "未监听"}
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
        <p
          className={`message ${messageTone === "warning" || status?.lastError ? "warning" : ""}`}
          role={
            messageTone === "warning" || status?.lastError ? "alert" : "status"
          }
        >
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
      {diagnosticConsentOpen && diagnosticResult && (
        <div className="modal-backdrop" role="presentation">
          <section
            aria-labelledby="diagnostic-consent-title"
            aria-modal="true"
            className="confirm-dialog diagnostic-dialog"
            role="dialog"
          >
            <h2 id="diagnostic-consent-title">检测到网络异常</h2>
            <p>
              GitHub 或 Google
              站点无法访问。是否上传网络检测结果，帮助我们分析问题？
              仅上传检测状态、延迟和错误信息，不包含账号密码或代理凭据。
            </p>
            <div className="confirm-actions">
              <button
                className="cancel-button"
                disabled={diagnosticSubmitting}
                onClick={closeDiagnosticConsent}
                type="button"
              >
                暂不上传
              </button>
              <button
                className="confirm-button action-button"
                disabled={diagnosticSubmitting}
                onClick={() => void submitDiagnosticReport()}
                type="button"
              >
                {diagnosticSubmitting && <span className="button-spinner" />}
                {diagnosticSubmitting ? "上传中…" : "同意并上传"}
              </button>
            </div>
          </section>
        </div>
      )}
      {showProfile && (
        <div className="modal-backdrop" role="presentation">
          <section
            aria-labelledby="profile-dialog-title"
            aria-modal="true"
            className="confirm-dialog profile-dialog"
            role="dialog"
          >
            <h2 id="profile-dialog-title">个人信息</h2>
            <label className="profile-field" htmlFor="profile-name">
              <span>名称</span>
              <input
                disabled={profileBusy}
                id="profile-name"
                maxLength={80}
                onChange={(event) => setProfileName(event.target.value)}
                value={profileName}
              />
            </label>
            <div className="profile-field">
              <span>邮箱</span>
              <p>{session.user.email}</p>
            </div>
            {profileError && <p className="profile-error">{profileError}</p>}
            <div className="confirm-actions">
              <button
                className="cancel-button"
                disabled={profileBusy}
                onClick={() => setShowProfile(false)}
                type="button"
              >
                取消
              </button>
              <button
                className="confirm-button action-button"
                disabled={profileBusy}
                onClick={() => void submitProfile()}
                type="button"
              >
                {profileBusy && <span className="button-spinner" />}
                {profileBusy ? "保存中…" : "保存"}
              </button>
            </div>
          </section>
        </div>
      )}
      {showFeedback && (
        <div className="modal-backdrop" role="presentation">
          <section
            aria-labelledby="feedback-dialog-title"
            aria-modal="true"
            className="confirm-dialog feedback-dialog"
            role="dialog"
          >
            <div className="feedback-dialog-heading">
              <h2 id="feedback-dialog-title">问题反馈</h2>
              <button
                aria-label="关闭问题反馈"
                className="feedback-close-button"
                disabled={feedbackBusy}
                onClick={() => setShowFeedback(false)}
                title="关闭"
                type="button"
              >
                ×
              </button>
            </div>
            <p className="feedback-dialog-intro">
              描述问题，可附最多 3 张截图。
            </p>
            <textarea
              aria-label="问题描述"
              className="feedback-textarea"
              disabled={feedbackBusy}
              maxLength={5000}
              onChange={(event) => setFeedbackText(event.target.value)}
              placeholder="例如：开启代理后无法访问某个网站，并说明出现的提示…"
              rows={3}
              value={feedbackText}
            />
            <div className="feedback-upload-row">
              <label className="feedback-upload-button">
                添加截图
                <input
                  accept="image/png,image/jpeg,image/webp"
                  disabled={feedbackBusy || feedbackFiles.length >= 3}
                  multiple
                  onChange={selectFeedbackScreenshots}
                  type="file"
                />
              </label>
              <small>{feedbackFiles.length}/3 张，单张最大 5 MiB</small>
            </div>
            {feedbackFiles.length > 0 && (
              <ul className="feedback-files">
                {feedbackFiles.map((file, index) => (
                  <li key={`${file.name}-${file.lastModified}`}>
                    <span>{file.name}</span>
                    <button
                      aria-label={`移除 ${file.name}`}
                      disabled={feedbackBusy}
                      onClick={() =>
                        setFeedbackFiles((files) =>
                          files.filter((_, itemIndex) => itemIndex !== index),
                        )
                      }
                      type="button"
                    >
                      移除
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {feedbackError && <p className="feedback-error">{feedbackError}</p>}
            {feedbackItems.length > 0 && (
              <div className="feedback-history">
                <strong>我的反馈</strong>
                {feedbackItems.slice(0, 5).map((item) => (
                  <article key={item.id} className="feedback-history-item">
                    <p>{item.message}</p>
                    <small>
                      {new Date(item.created_at).toLocaleString("zh-CN", {
                        month: "numeric",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                      {item.status === "replied" ? " · 已回复" : " · 待回复"}
                    </small>
                    {item.reply && (
                      <div className="feedback-reply">回复：{item.reply}</div>
                    )}
                  </article>
                ))}
              </div>
            )}
            <div className="confirm-actions">
              <button
                className="confirm-button feedback-submit-button"
                disabled={feedbackBusy}
                onClick={() => void submitFeedback()}
                type="button"
              >
                {feedbackBusy && <span className="button-spinner" />}
                {feedbackBusy ? "提交中…" : "提交反馈"}
              </button>
            </div>
          </section>
        </div>
      )}
      {updateDialogOpen && (
        <div className="modal-backdrop" role="presentation">
          <section
            aria-labelledby="update-dialog-title"
            aria-modal="true"
            className="confirm-dialog update-dialog"
            role="dialog"
          >
            <h2 id="update-dialog-title">
              {updateState === "latest"
                ? "暂时没有新版本需要更新"
                : updateState === "error"
                  ? "检查更新失败"
                  : availableUpdate
                    ? `发现 GoYou ${availableUpdate.version}`
                    : "检查 GoYou 更新"}
            </h2>
            {updateState === "checking" && <p>正在连接官方更新服务，请稍候…</p>}
            {updateState === "latest" && (
              <p>当前版本 v{appPackage.version} 暂时没有新版本需要更新。</p>
            )}
            {updateState === "error" && (
              <p>{updateError ?? "暂时无法获取更新信息，请稍后重试。"}</p>
            )}
            {availableUpdate &&
              updateState !== "error" &&
              updateState !== "checking" && (
                <>
                  <p className="update-notes">
                    {availableUpdate.body || "本次更新包含稳定性和体验改进。"}
                  </p>
                  {(updateState === "downloading" ||
                    updateState === "installing") && (
                    <div
                      className="update-progress"
                      aria-label={`已下载 ${Math.round(updateProgress)}%`}
                    >
                      <span style={{ width: `${updateProgress}%` }} />
                    </div>
                  )}
                  {updateState === "downloading" && (
                    <small>正在下载更新… {Math.round(updateProgress)}%</small>
                  )}
                  {updateState === "installing" && (
                    <small>正在安装并重启 GoYou…</small>
                  )}
                </>
              )}
            <div className="confirm-actions">
              {updateState !== "downloading" &&
                updateState !== "installing" && (
                  <button
                    className="cancel-button"
                    onClick={() => void closeUpdateDialog()}
                    type="button"
                  >
                    关闭
                  </button>
                )}
              {updateState === "available" && (
                <button
                  className="confirm-button action-button"
                  onClick={() => void installUpdate()}
                  type="button"
                >
                  更新并重启
                </button>
              )}
              {updateState === "error" && (
                <button
                  className="confirm-button action-button"
                  onClick={() => void checkForUpdates()}
                  type="button"
                >
                  重试
                </button>
              )}
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
