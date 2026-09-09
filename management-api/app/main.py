from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr, Field


APP_NAME = os.getenv("APP_NAME", "GoYou Management API")
JWT_SECRET = os.getenv("JWT_SECRET", "")
if len(JWT_SECRET) < 32:
    raise RuntimeError("JWT_SECRET must be at least 32 characters")
_configured_db_path = os.getenv("DB_PATH")
if _configured_db_path:
    DB_PATH = Path(_configured_db_path)
else:
    _goyou_db_path = Path("/data/goyou.sqlite3")
    _legacy_db_path = Path("/data/proxyswitch.sqlite3")
    # Keep existing deployments on their original database until an explicit
    # DB_PATH is supplied; new installations use the GoYou filename.
    DB_PATH = _legacy_db_path if _legacy_db_path.exists() and not _goyou_db_path.exists() else _goyou_db_path
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "15"))
REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_DAYS", "30"))
RELAY_HOST = os.getenv("RELAY_HOST", "relay.123371.com")
RELAY_PORT = int(os.getenv("RELAY_PORT", "24443"))
RELAY_METHOD = os.getenv("RELAY_METHOD", "chacha20-ietf-poly1305")
RELAY_PASSWORD = os.getenv("RELAY_PASSWORD", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with db() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS leases (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                device_id TEXT,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            """
        )


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class DeviceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    platform: str = Field(default="desktop", max_length=40)


class LeaseRequest(BaseModel):
    device_id: str | None = Field(default=None, max_length=100)


class LeaseRefreshRequest(BaseModel):
    lease_id: str = Field(min_length=8, max_length=100)


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AdminUserStatusRequest(BaseModel):
    enabled: bool


def public_user(row: sqlite3.Row) -> dict[str, str]:
    return {"id": row["id"], "email": row["email"], "name": row["name"], "created_at": row["created_at"]}


def access_token(user_id: str) -> str:
    issued = now()
    payload = {"sub": user_id, "type": "access", "iat": issued, "exp": issued + timedelta(minutes=ACCESS_TOKEN_MINUTES)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def admin_access_token() -> str:
    issued = now()
    payload = {"sub": "admin", "type": "admin_access", "iat": issued, "exp": issued + timedelta(minutes=ACCESS_TOKEN_MINUTES)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def issue_refresh_token(user_id: str) -> str:
    token = secrets.token_urlsafe(48)
    expires = now() + timedelta(days=REFRESH_TOKEN_DAYS)
    with db() as connection:
        connection.execute("INSERT INTO refresh_tokens(token_hash, user_id, expires_at) VALUES (?, ?, ?)", (hash_token(token), user_id, iso(expires)))
    return token


def auth_response(row: sqlite3.Row) -> dict:
    return {"access_token": access_token(row["id"]), "refresh_token": issue_refresh_token(row["id"]), "token_type": "bearer", "user": public_user(row)}


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def current_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> sqlite3.Row:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") != "access" or not payload.get("sub"):
            raise ValueError
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期") from None
    with db() as connection:
        row = connection.execute("SELECT * FROM users WHERE id = ? AND disabled = 0", (payload["sub"],)).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不可用")
    return row


def current_admin(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> dict[str, str]:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        if payload.get("type") != "admin_access" or payload.get("sub") != "admin":
            raise ValueError
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员登录已过期") from None
    return {"email": ADMIN_EMAIL, "name": "GoYou 管理员"}


def verify_admin_password(password: str) -> bool:
    if ADMIN_PASSWORD_HASH:
        try:
            return password_hasher.verify(ADMIN_PASSWORD_HASH, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    return bool(ADMIN_PASSWORD) and secrets.compare_digest(password, ADMIN_PASSWORD)


app = FastAPI(title=APP_NAME, version="0.1.0")
origins = [value.strip() for value in os.getenv("CORS_ORIGINS", "").split(",") if value.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest) -> dict:
    email = str(payload.email).lower()
    name = payload.name.strip() or email.split("@", 1)[0]
    user_id = secrets.token_hex(16)
    created_at = iso(now())
    try:
        with db() as connection:
            connection.execute("INSERT INTO users(id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)", (user_id, email, name, password_hasher.hash(payload.password), created_at))
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该邮箱已经注册，请直接登录") from None
    return auth_response(row)


@app.post("/v1/auth/login")
def login(payload: LoginRequest) -> dict:
    email = str(payload.email).lower()
    with db() as connection:
        row = connection.execute("SELECT * FROM users WHERE email = ? AND disabled = 0", (email,)).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码不正确")
    try:
        password_hasher.verify(row["password_hash"], payload.password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码不正确") from None
    return auth_response(row)


@app.post("/v1/auth/refresh")
def refresh(payload: RefreshRequest) -> dict:
    token_hash = hash_token(payload.refresh_token)
    with db() as connection:
        row = connection.execute("SELECT u.*, r.expires_at, r.revoked_at FROM refresh_tokens r JOIN users u ON u.id = r.user_id WHERE r.token_hash = ?", (token_hash,)).fetchone()
        if not row or row["revoked_at"] or datetime.fromisoformat(row["expires_at"]) <= now() or row["disabled"]:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新令牌无效或已过期")
        connection.execute("UPDATE refresh_tokens SET revoked_at = ? WHERE token_hash = ?", (iso(now()), token_hash))
    return auth_response(row)


@app.post("/v1/auth/logout")
def logout(user: Annotated[sqlite3.Row, Depends(current_user)]) -> None:
    with db() as connection:
        connection.execute("UPDATE refresh_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (iso(now()), user["id"]))


@app.get("/v1/me")
def me(user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    return {"user": public_user(user)}


@app.post("/v1/devices/register")
def register_device(payload: DeviceRequest, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    return {"device_id": secrets.token_hex(16), "name": payload.name, "platform": payload.platform, "user_id": user["id"]}


@app.post("/v1/proxy/lease")
def create_lease(payload: LeaseRequest, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    if not RELAY_PASSWORD:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="代理节点尚未配置租约凭据")
    lease_id = secrets.token_hex(16)
    expires = now() + timedelta(hours=1)
    username = "relay"
    password = RELAY_PASSWORD
    with db() as connection:
        connection.execute("INSERT INTO leases(id, user_id, device_id, username, password, expires_at) VALUES (?, ?, ?, ?, ?, ?)", (lease_id, user["id"], payload.device_id, username, password, iso(expires)))
    return {"lease_id": lease_id, "host": RELAY_HOST, "port": RELAY_PORT, "method": RELAY_METHOD, "username": username, "password": password, "expires_at": iso(expires)}


@app.post("/v1/proxy/lease/refresh")
def refresh_lease(payload: LeaseRefreshRequest, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    if not RELAY_PASSWORD:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="代理节点尚未配置租约凭据")
    expires = now() + timedelta(hours=1)
    with db() as connection:
        lease = connection.execute("SELECT id, device_id, revoked_at FROM leases WHERE id = ? AND user_id = ?", (payload.lease_id, user["id"])).fetchone()
        if not lease or lease["revoked_at"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租约不存在或已撤销")
        connection.execute("UPDATE leases SET expires_at = ?, password = ? WHERE id = ?", (iso(expires), RELAY_PASSWORD, payload.lease_id))
    return {"lease_id": payload.lease_id, "device_id": lease["device_id"], "host": RELAY_HOST, "port": RELAY_PORT, "method": RELAY_METHOD, "username": "relay", "password": RELAY_PASSWORD, "expires_at": iso(expires)}


@app.post("/v1/admin/auth/login")
def admin_login(payload: AdminLoginRequest) -> dict:
    email = str(payload.email).lower()
    if not ADMIN_EMAIL or not (ADMIN_PASSWORD or ADMIN_PASSWORD_HASH):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="管理员账号尚未配置")
    if email != ADMIN_EMAIL or not verify_admin_password(payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理员邮箱或密码不正确")
    return {
        "access_token": admin_access_token(),
        "token_type": "bearer",
        "user": {"email": ADMIN_EMAIL, "name": "GoYou 管理员"},
    }


@app.get("/v1/admin/auth/me")
def admin_me(admin: Annotated[dict[str, str], Depends(current_admin)]) -> dict:
    return {"user": admin}


@app.post("/v1/admin/auth/logout")
def admin_logout(admin: Annotated[dict[str, str], Depends(current_admin)]) -> None:
    return None


def lease_state(row: sqlite3.Row) -> str:
    if row["revoked_at"]:
        return "revoked"
    try:
        if datetime.fromisoformat(row["expires_at"]) <= now():
            return "expired"
    except (TypeError, ValueError):
        return "unknown"
    return "active"


@app.get("/v1/admin/overview")
def admin_overview(admin: Annotated[dict[str, str], Depends(current_admin)]) -> dict:
    with db() as connection:
        users = connection.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN disabled = 0 THEN 1 ELSE 0 END) AS enabled FROM users").fetchone()
        leases = connection.execute("SELECT id, expires_at, revoked_at FROM leases").fetchall()
        refresh_tokens = connection.execute("SELECT COUNT(*) AS total FROM refresh_tokens WHERE revoked_at IS NULL AND expires_at > ?", (iso(now()),)).fetchone()["total"]
    states = {"active": 0, "expired": 0, "revoked": 0, "unknown": 0}
    for lease in leases:
        states[lease_state(lease)] += 1
    return {
        "service": {"name": APP_NAME, "status": "ok", "relay_host": RELAY_HOST, "relay_port": RELAY_PORT, "relay_method": RELAY_METHOD},
        "users": {"total": users["total"], "enabled": users["enabled"] or 0, "disabled": (users["total"] or 0) - (users["enabled"] or 0)},
        "leases": {"total": len(leases), **states},
        "active_refresh_tokens": refresh_tokens,
        "generated_at": iso(now()),
    }


@app.get("/v1/admin/users")
def admin_users(
    admin: Annotated[dict[str, str], Depends(current_admin)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str = Query(default="", max_length=100),
    state: str = Query(default="", pattern="^(|active|disabled)$"),
) -> dict:
    conditions = ["1 = 1"]
    params: list[str | int] = []
    if keyword.strip():
        conditions.append("(u.email LIKE ? OR u.name LIKE ? OR u.id LIKE ?)")
        term = f"%{keyword.strip()}%"
        params.extend([term, term, term])
    if state == "active":
        conditions.append("u.disabled = 0")
    elif state == "disabled":
        conditions.append("u.disabled = 1")
    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    with db() as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM users u WHERE {where}", params).fetchone()[0]
        rows = connection.execute(
            f"""SELECT u.id, u.email, u.name, u.created_at, u.disabled, COUNT(l.id) AS lease_count
                FROM users u LEFT JOIN leases l ON l.user_id = u.id
                WHERE {where}
                GROUP BY u.id ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",
            [*params, page_size, offset],
        ).fetchall()
    return {
        "items": [
            {"id": row["id"], "email": row["email"], "name": row["name"], "created_at": row["created_at"], "enabled": not bool(row["disabled"]), "lease_count": row["lease_count"]}
            for row in rows
        ],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@app.patch("/v1/admin/users/{user_id}/status")
def admin_user_status(user_id: str, payload: AdminUserStatusRequest, admin: Annotated[dict[str, str], Depends(current_admin)]) -> dict:
    with db() as connection:
        cursor = connection.execute("UPDATE users SET disabled = ? WHERE id = ?", (0 if payload.enabled else 1, user_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        row = connection.execute("SELECT id, email, name, created_at, disabled FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"id": row["id"], "email": row["email"], "name": row["name"], "created_at": row["created_at"], "enabled": not bool(row["disabled"])}


@app.get("/v1/admin/leases")
def admin_leases(
    admin: Annotated[dict[str, str], Depends(current_admin)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str = Query(default="", max_length=100),
    state: str = Query(default="", pattern="^(|active|expired|revoked)$"),
) -> dict:
    conditions = ["1 = 1"]
    params: list[str | int] = []
    if keyword.strip():
        conditions.append("(l.id LIKE ? OR u.email LIKE ? OR COALESCE(l.device_id, '') LIKE ?)")
        term = f"%{keyword.strip()}%"
        params.extend([term, term, term])
    if state == "revoked":
        conditions.append("l.revoked_at IS NOT NULL")
    elif state == "expired":
        conditions.extend(["l.revoked_at IS NULL", "l.expires_at <= ?"])
        params.append(iso(now()))
    elif state == "active":
        conditions.extend(["l.revoked_at IS NULL", "l.expires_at > ?"])
        params.append(iso(now()))
    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    with db() as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM leases l JOIN users u ON u.id = l.user_id WHERE {where}", params).fetchone()[0]
        rows = connection.execute(
            f"""SELECT l.id, l.user_id, l.device_id, l.expires_at, l.revoked_at, u.email, u.name
                FROM leases l JOIN users u ON u.id = l.user_id
                WHERE {where}
                ORDER BY l.expires_at DESC LIMIT ? OFFSET ?""",
            [*params, page_size, offset],
        ).fetchall()
    return {
        "items": [
            {"id": row["id"], "user_id": row["user_id"], "email": row["email"], "name": row["name"], "device_id": row["device_id"], "expires_at": row["expires_at"], "revoked_at": row["revoked_at"], "state": lease_state(row)}
            for row in rows
        ],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@app.post("/v1/admin/leases/{lease_id}/revoke")
def admin_revoke_lease(lease_id: str, admin: Annotated[dict[str, str], Depends(current_admin)]) -> dict:
    with db() as connection:
        cursor = connection.execute("UPDATE leases SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?", (iso(now()), lease_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租约不存在")
        row = connection.execute("SELECT id, user_id, expires_at, revoked_at FROM leases WHERE id = ?", (lease_id,)).fetchone()
    return {"id": row["id"], "user_id": row["user_id"], "expires_at": row["expires_at"], "revoked_at": row["revoked_at"], "state": lease_state(row)}
