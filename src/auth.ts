export interface AuthUser {
  id: string;
  email: string;
  name: string;
  created_at: string;
}

export interface AuthSession {
  token: string;
  refreshToken: string;
  user: AuthUser;
  lease: ProxyLease;
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
}

interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: AuthUser;
}

const SESSION_KEY = "goyou.auth.session";
const LEGACY_SESSION_KEY = "proxyswitch.auth.session";
export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || "https://proxy.123371.com"
).replace(/\/$/, "");

async function request<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    });
  } catch {
    throw new Error("无法连接管理服务器，请检查网络或稍后重试");
  }
  const body = (await response.json().catch(() => null)) as
    | { detail?: string }
    | T
    | null;
  if (!response.ok) {
    const detail =
      body && typeof body === "object" && "detail" in body
        ? body.detail
        : undefined;
    throw new Error(detail || `管理服务器返回错误（${response.status}）`);
  }
  return body as T;
}

function saveSession(
  response: AuthResponse & { lease: ProxyLease },
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
      !session.user.email ||
      !session.lease?.host
    )
      return null;
    return session as AuthSession;
  } catch {
    return null;
  }
}

export async function register(
  name: string,
  email: string,
  password: string,
): Promise<AuthSession> {
  const response = await request<AuthResponse>("/v1/auth/register", {
    method: "POST",
    body: JSON.stringify({
      name: name.trim(),
      email: email.trim().toLowerCase(),
      password,
    }),
  });
  return authenticate(response);
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
  const lease = await request<ProxyLease>("/v1/proxy/lease/refresh", {
    method: "POST",
    headers: { Authorization: `Bearer ${response.access_token}` },
    body: JSON.stringify({ lease_id: session.lease.lease_id }),
  });
  return saveSession({ ...response, lease });
}

export async function getTodayUsage(
  session: AuthSession,
): Promise<UsageSummary> {
  return request<UsageSummary>("/v1/usage/today", {
    method: "GET",
    headers: { Authorization: `Bearer ${session.token}` },
  });
}

async function authenticate(response: AuthResponse): Promise<AuthSession> {
  const lease = await request<ProxyLease>("/v1/proxy/lease", {
    method: "POST",
    headers: { Authorization: `Bearer ${response.access_token}` },
    body: JSON.stringify({ device_id: crypto.randomUUID() }),
  });
  return saveSession({ ...response, lease });
}

export async function logout(): Promise<void> {
  const session = getSession();
  localStorage.removeItem(SESSION_KEY);
  if (!session) return;
  await fetch(`${API_BASE_URL}/v1/auth/logout`, {
    method: "POST",
    headers: { Authorization: `Bearer ${session.token}` },
  }).catch(() => undefined);
}
