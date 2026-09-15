import { invoke } from "@tauri-apps/api/core";

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  created_at: string;
  account_expires_at: string | null;
}

export interface AuthSession {
  token: string;
  refreshToken: string;
  user: AuthUser;
  lease: ProxyLease | null;
}

export interface ProxyLease {
  lease_id: string;
  host: string;
  port: number;
  method: string;
  username: string;
  password: string;
  expires_at: string;
  quota_exceeded?: boolean;
}

export interface UsageSummary {
  date: string;
  used_bytes: number;
  upload_bytes: number;
  download_bytes: number;
  quota_bytes: number;
  remaining_bytes: number;
  percentage: number;
  exceeded: boolean;
  exceeded_at: string | null;
  resets_at: string;
  timezone: string;
  account_expires_at?: string | null;
  account_expired?: boolean;
}

export interface FeedbackScreenshot {
  filename: string;
  data: string;
}

export interface FeedbackAttachment {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
}

export interface FeedbackItem {
  id: string;
  message: string;
  status: "open" | "replied";
  reply: string | null;
  created_at: string;
  updated_at: string;
  replied_at: string | null;
  attachments: FeedbackAttachment[];
}

interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: AuthUser;
}

const SESSION_KEY = "goyou.auth.session";
const LEGACY_SESSION_KEY = "proxyswitch.auth.session";
const REMEMBERED_LOGIN_KEY = "goyou.login.remembered";
const DEVICE_ID_KEY = "goyou.device.id";
const LEGACY_DEVICE_ID_KEY = "proxyswitch.device.id";
export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || "https://proxy.123371.com"
).replace(/\/$/, "");

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const headers = new Headers(init.headers);
  const authorization = headers.get("Authorization");
  const body = init.body ? JSON.parse(String(init.body)) : undefined;
  try {
    return await invoke<T>("control_request", {
      path,
      method: init.method ?? "GET",
      body,
      accessToken: authorization?.replace(/^Bearer\s+/i, "") ?? null,
    });
  } catch (error) {
    if (error instanceof Error && /超时/.test(error.message)) {
      throw error;
    }
    throw new Error(error instanceof Error ? error.message : String(error));
  }
}

function saveSession(
  response: AuthResponse & { lease: ProxyLease | null },
): AuthSession {
  const session: AuthSession = {
    token: response.access_token,
    refreshToken: response.refresh_token,
    user: response.user,
    lease: response.lease,
  };
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  localStorage.removeItem(LEGACY_SESSION_KEY);
  return session;
}

export function getSession(): AuthSession | null {
  try {
    const currentValue = localStorage.getItem(SESSION_KEY);
    const legacyValue = localStorage.getItem(LEGACY_SESSION_KEY);
    const value = currentValue ?? legacyValue;
    if (!value) return null;
    if (!currentValue && legacyValue) {
      localStorage.setItem(SESSION_KEY, legacyValue);
      localStorage.removeItem(LEGACY_SESSION_KEY);
    }
    const session = JSON.parse(value) as Partial<AuthSession>;
    if (
      !session.token ||
      !session.refreshToken ||
      !session.user?.id ||
      !session.user.email
    )
      return null;
    return session as AuthSession;
  } catch {
    return null;
  }
}

export interface RememberedLogin {
  email: string;
  password: string;
}

export function getRememberedLogin(): RememberedLogin | null {
  try {
    const value = localStorage.getItem(REMEMBERED_LOGIN_KEY);
    if (!value) return null;
    const remembered = JSON.parse(value) as Partial<RememberedLogin>;
    if (!remembered.email || !remembered.password) return null;
    return { email: remembered.email, password: remembered.password };
  } catch {
    return null;
  }
}

export function saveRememberedLogin(email: string, password: string): void {
  localStorage.setItem(
    REMEMBERED_LOGIN_KEY,
    JSON.stringify({ email: email.trim(), password }),
  );
}

export function clearRememberedLogin(): void {
  localStorage.removeItem(REMEMBERED_LOGIN_KEY);
}

export async function register(
  name: string,
  email: string,
  verificationCode: string,
  password: string,
): Promise<AuthSession> {
  const response = await request<AuthResponse>("/v1/auth/register", {
    method: "POST",
    body: JSON.stringify({
      name: name.trim(),
      email: email.trim().toLowerCase(),
      verification_code: verificationCode,
      password,
    }),
  });
  return authenticate(response);
}

export async function requestRegistrationCode(email: string): Promise<void> {
  await request<{ message: string }>("/v1/auth/register/send-code", {
    method: "POST",
    body: JSON.stringify({ email: email.trim().toLowerCase() }),
  });
}

export async function requestPasswordResetCode(email: string): Promise<void> {
  await request<{ message: string }>("/v1/auth/password-reset/send-code", {
    method: "POST",
    body: JSON.stringify({ email: email.trim().toLowerCase() }),
  });
}

export async function resetPassword(
  email: string,
  verificationCode: string,
  password: string,
): Promise<void> {
  await request<{ message: string }>("/v1/auth/password-reset", {
    method: "POST",
    body: JSON.stringify({
      email: email.trim().toLowerCase(),
      verification_code: verificationCode,
      password,
    }),
  });
}

export async function login(
  email: string,
  password: string,
): Promise<AuthSession> {
  const response = await request<AuthResponse>("/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
  });
  return authenticate(response);
}

export async function refreshSession(
  session: AuthSession,
): Promise<AuthSession> {
  const response = await request<AuthResponse>("/v1/auth/refresh", {
    method: "POST",
    body: JSON.stringify({ refresh_token: session.refreshToken }),
  });
  let lease: ProxyLease | null = null;
  if (session.lease) {
    try {
      lease = await request<ProxyLease>("/v1/proxy/lease/refresh", {
        method: "POST",
        headers: { Authorization: `Bearer ${response.access_token}` },
        body: JSON.stringify({ lease_id: session.lease.lease_id }),
      });
    } catch (error) {
      if (!isAccountExpiredError(error)) throw error;
    }
  } else {
    try {
      lease = await request<ProxyLease>("/v1/proxy/lease", {
        method: "POST",
        headers: { Authorization: `Bearer ${response.access_token}` },
        body: JSON.stringify({ device_id: getDeviceId() }),
      });
    } catch (error) {
      if (!isAccountExpiredError(error)) throw error;
    }
  }
  return saveSession({ ...response, lease });
}

export async function getTodayUsage(
  session: AuthSession,
): Promise<UsageSummary> {
  return invoke<UsageSummary>("get_goyou_usage", {
    accessToken: session.token,
  });
}

export async function updateProfile(
  session: AuthSession,
  name: string,
): Promise<AuthSession> {
  const response = await request<{ user: AuthUser }>("/v1/me", {
    method: "PATCH",
    headers: { Authorization: `Bearer ${session.token}` },
    body: JSON.stringify({ name: name.trim() }),
  });
  return saveSession({
    access_token: session.token,
    refresh_token: session.refreshToken,
    user: response.user,
    lease: session.lease,
  });
}

export async function getFeedback(
  session: AuthSession,
): Promise<FeedbackItem[]> {
  const response = await request<{ items: FeedbackItem[] }>("/v1/feedback", {
    method: "GET",
    headers: { Authorization: `Bearer ${session.token}` },
  });
  return response.items;
}

export async function createFeedback(
  session: AuthSession,
  message: string,
  screenshots: FeedbackScreenshot[],
): Promise<FeedbackItem> {
  return request<FeedbackItem>("/v1/feedback", {
    method: "POST",
    headers: { Authorization: `Bearer ${session.token}` },
    body: JSON.stringify({ message, screenshots }),
  });
}

async function authenticate(response: AuthResponse): Promise<AuthSession> {
  let lease: ProxyLease | null = null;
  try {
    lease = await request<ProxyLease>("/v1/proxy/lease", {
      method: "POST",
      headers: { Authorization: `Bearer ${response.access_token}` },
      body: JSON.stringify({ device_id: getDeviceId() }),
    });
  } catch (error) {
    if (!isAccountExpiredError(error)) throw error;
  }
  return saveSession({ ...response, lease });
}

function isAccountExpiredError(error: unknown): boolean {
  return error instanceof Error && /账户已到期/.test(error.message);
}

function getDeviceId(): string {
  const existing =
    localStorage.getItem(DEVICE_ID_KEY) ||
    localStorage.getItem(LEGACY_DEVICE_ID_KEY);
  if (existing) {
    localStorage.setItem(DEVICE_ID_KEY, existing);
    localStorage.removeItem(LEGACY_DEVICE_ID_KEY);
    return existing;
  }
  const deviceId = crypto.randomUUID();
  localStorage.setItem(DEVICE_ID_KEY, deviceId);
  return deviceId;
}

export async function logout(): Promise<void> {
  const session = getSession();
  localStorage.removeItem(SESSION_KEY);
  if (!session) return;
  await request("/v1/auth/logout", {
    method: "POST",
    headers: { Authorization: `Bearer ${session.token}` },
  }).catch(() => undefined);
}
