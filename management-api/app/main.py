from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Query, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import FileResponse
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
RELAY_PORT_START = int(os.getenv("RELAY_PORT_START", "30000"))
RELAY_PORT_END = int(os.getenv("RELAY_PORT_END", "39999"))
RELAY_METHOD = os.getenv("RELAY_METHOD", "chacha20-ietf-poly1305")
RELAY_PASSWORD = os.getenv("RELAY_PASSWORD", "")
DEFAULT_DAILY_QUOTA_BYTES = int(os.getenv("DEFAULT_DAILY_QUOTA_BYTES", "500000000"))
DEFAULT_ACCOUNT_VALID_DAYS = int(os.getenv("DEFAULT_ACCOUNT_VALID_DAYS", "365"))
QUOTA_TIMEZONE = os.getenv("QUOTA_TIMEZONE", "Asia/Shanghai")
METERING_TOKEN = os.getenv("METERING_TOKEN", "").strip()
UPDATE_GITHUB_REPOSITORY = os.getenv("UPDATE_GITHUB_REPOSITORY", "zzktz/goyou").strip()
UPDATE_STORAGE_DIR = Path(os.getenv("UPDATE_STORAGE_DIR", str(DB_PATH.parent / "updates")))
UPDATE_MAX_ASSET_BYTES = int(os.getenv("UPDATE_MAX_ASSET_BYTES", "2000000000"))
UPDATE_PLATFORMS = ("windows-x86_64", "darwin-aarch64", "darwin-x86_64")
UPDATE_PUBLIC_BASE_URL = os.getenv("UPDATE_PUBLIC_BASE_URL", "https://proxy.123371.com").rstrip("/")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")
password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def quota_zone() -> ZoneInfo:
    try:
        return ZoneInfo(QUOTA_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def usage_date(value: datetime | None = None) -> str:
    return (value or now()).astimezone(quota_zone()).date().isoformat()


def usage_day_bounds(target_date: str) -> tuple[str, str]:
    day = datetime.strptime(target_date, "%Y-%m-%d").date()
    zone = quota_zone()
    start = datetime.combine(day, datetime.min.time(), tzinfo=zone)
    end = start + timedelta(days=1)
    return iso(start), iso(end)


def next_reset_at() -> str:
    local_now = now().astimezone(quota_zone())
    next_day = local_now.date() + timedelta(days=1)
    reset = datetime.combine(next_day, datetime.min.time(), tzinfo=quota_zone())
    return reset.isoformat()


def default_account_expires_at(created_at: datetime) -> str:
    local_created = created_at.astimezone(quota_zone())
    expiry_date = local_created.date() + timedelta(days=DEFAULT_ACCOUNT_VALID_DAYS)
    expiry = datetime.combine(expiry_date, datetime.max.time().replace(microsecond=0), tzinfo=quota_zone())
    return iso(expiry)


def account_expiry_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(quota_zone()).date().isoformat()
    except (TypeError, ValueError):
        return None


def account_expiry_value(value: str | None, *, default: str | None = None) -> str | None:
    if not value:
        return default
    try:
        expiry_date = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="到期日期无效") from None
    return iso(datetime.combine(expiry_date, datetime.max.time().replace(microsecond=0), tzinfo=quota_zone()))


def account_is_expired(value: str | None) -> bool:
    if not value:
        return False
    try:
        return datetime.fromisoformat(value) <= now()
    except (TypeError, ValueError):
        return True


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def ensure_user_quota(connection: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    connection.execute(
        "INSERT OR IGNORE INTO user_quotas(user_id, daily_limit_bytes, timezone, updated_at) VALUES (?, ?, ?, ?)",
        (user_id, DEFAULT_DAILY_QUOTA_BYTES, QUOTA_TIMEZONE, iso(now())),
    )
    return connection.execute("SELECT * FROM user_quotas WHERE user_id = ?", (user_id,)).fetchone()


def allocate_relay_port(connection: sqlite3.Connection) -> int:
    used = {
        int(row[0])
        for row in connection.execute(
            "SELECT relay_port FROM leases WHERE revoked_at IS NULL AND expires_at > ? AND relay_port IS NOT NULL",
            (iso(now()),),
        ).fetchall()
    }
    for port in range(RELAY_PORT_START, RELAY_PORT_END + 1):
        if port != RELAY_PORT and port not in used:
            return port
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="代理节点当前没有可用端口")


def new_relay_credentials(lease_id: str) -> tuple[str, str]:
    return f"goyou-{lease_id[:12]}", secrets.token_urlsafe(32)


def usage_payload(connection: sqlite3.Connection, user_id: str, date: str | None = None) -> dict:
    current_date = date or usage_date()
    quota = ensure_user_quota(connection, user_id)
    usage = connection.execute(
        "SELECT * FROM daily_usage WHERE user_id = ? AND usage_date = ?",
        (user_id, current_date),
    ).fetchone()
    if not usage:
        used = 0
        upload = 0
        download = 0
        exceeded_at = None
    else:
        used = int(usage["total_bytes"])
        upload = int(usage["upload_bytes"])
        download = int(usage["download_bytes"])
        exceeded_at = usage["exceeded_at"]
    limit = int(quota["daily_limit_bytes"])
    exceeded = used >= limit
    return {
        "date": current_date,
        "used_bytes": used,
        "upload_bytes": upload,
        "download_bytes": download,
        "quota_bytes": limit,
        "remaining_bytes": max(limit - used, 0),
        "percentage": round((used / limit) * 100, 2) if limit else 100,
        "exceeded": exceeded,
        "exceeded_at": exceeded_at,
        "resets_at": next_reset_at(),
        "timezone": quota["timezone"],
    }


def user_usage(user_id: str, date: str | None = None) -> dict:
    with db() as connection:
        return usage_payload(connection, user_id, date)


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
                disabled INTEGER NOT NULL DEFAULT 0,
                account_expires_at TEXT
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
                relay_port INTEGER,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS user_quotas (
                user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                daily_limit_bytes INTEGER NOT NULL DEFAULT 500000000,
                timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                usage_date TEXT NOT NULL,
                upload_bytes INTEGER NOT NULL DEFAULT 0,
                download_bytes INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                last_reported_at TEXT,
                exceeded_at TEXT,
                PRIMARY KEY (user_id, usage_date)
            );
            CREATE TABLE IF NOT EXISTS usage_reports (
                report_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                lease_id TEXT,
                device_id TEXT,
                connection_id TEXT,
                target_host TEXT,
                target_port INTEGER,
                upload_bytes INTEGER NOT NULL,
                download_bytes INTEGER NOT NULL,
                reported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_releases (
                id TEXT PRIMARY KEY,
                version TEXT NOT NULL UNIQUE,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                download_status TEXT NOT NULL DEFAULT 'manual',
                download_platform TEXT,
                download_error TEXT,
                download_started_at TEXT,
                download_finished_at TEXT,
                created_at TEXT NOT NULL,
                published_at TEXT
            );
            CREATE TABLE IF NOT EXISTS app_release_assets (
                release_id TEXT NOT NULL REFERENCES app_releases(id) ON DELETE CASCADE,
                platform TEXT NOT NULL,
                filename TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                signature TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (release_id, platform)
            );
            CREATE INDEX IF NOT EXISTS idx_daily_usage_date ON daily_usage(usage_date);
            CREATE INDEX IF NOT EXISTS idx_usage_reports_reported_at ON usage_reports(reported_at);
            CREATE INDEX IF NOT EXISTS idx_usage_reports_user_id ON usage_reports(user_id);
            CREATE INDEX IF NOT EXISTS idx_app_releases_status ON app_releases(status, published_at);
            """
        )
        lease_columns = {row[1] for row in connection.execute("PRAGMA table_info(leases)").fetchall()}
        if "relay_port" not in lease_columns:
            connection.execute("ALTER TABLE leases ADD COLUMN relay_port INTEGER")
        user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()}
        added_expiry_column = "account_expires_at" not in user_columns
        if added_expiry_column:
            connection.execute("ALTER TABLE users ADD COLUMN account_expires_at TEXT")
            users_without_expiry = connection.execute(
                "SELECT id, created_at FROM users WHERE account_expires_at IS NULL"
            ).fetchall()
            for user in users_without_expiry:
                try:
                    created_at = datetime.fromisoformat(user["created_at"])
                    expiry = default_account_expires_at(created_at)
                except (TypeError, ValueError):
                    expiry = default_account_expires_at(now())
                connection.execute("UPDATE users SET account_expires_at = ? WHERE id = ?", (expiry, user["id"]))
        usage_report_columns = {row[1] for row in connection.execute("PRAGMA table_info(usage_reports)").fetchall()}
        for column, definition in (
            ("device_id", "TEXT"),
            ("connection_id", "TEXT"),
            ("target_host", "TEXT"),
            ("target_port", "INTEGER"),
        ):
            if column not in usage_report_columns:
                connection.execute(f"ALTER TABLE usage_reports ADD COLUMN {column} {definition}")
        release_columns = {row[1] for row in connection.execute("PRAGMA table_info(app_releases)").fetchall()}
        for column, definition in (
            ("download_status", "TEXT NOT NULL DEFAULT 'manual'"),
            ("download_platform", "TEXT"),
            ("download_error", "TEXT"),
            ("download_started_at", "TEXT"),
            ("download_finished_at", "TEXT"),
        ):
            if column not in release_columns:
                connection.execute(f"ALTER TABLE app_releases ADD COLUMN {column} {definition}")
        connection.execute(
            "UPDATE app_releases SET download_status = 'manual' WHERE download_status IS NULL OR download_status = ''"
        )
        connection.execute(
            """UPDATE app_releases
               SET download_status = 'failed', download_platform = NULL,
                   download_error = '后台下载任务已中断，请手动上传更新文件', download_finished_at = ?
               WHERE status = 'draft' AND download_status = 'downloading'""",
            (iso(now()),),
        )
        connection.execute(
            """UPDATE usage_reports SET device_id = (
                   SELECT device_id FROM leases WHERE leases.id = usage_reports.lease_id
               ) WHERE device_id IS NULL AND lease_id IS NOT NULL"""
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


class AdminCreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=80)
    account_expires_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    daily_limit_bytes: int = Field(default=DEFAULT_DAILY_QUOTA_BYTES, ge=0, le=10_000_000_000_000)


class AdminQuotaRequest(BaseModel):
    daily_limit_bytes: int = Field(ge=0, le=10_000_000_000_000)


class AdminAccountExpiryRequest(BaseModel):
    account_expires_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class AdminReleaseCreateRequest(BaseModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$", max_length=64)
    notes: str = Field(default="", max_length=10000)


class AdminReleaseUpdateRequest(BaseModel):
    notes: str = Field(default="", max_length=10000)


class AdminReleaseImportRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=10000)


class UsageReportRequest(BaseModel):
    report_id: str = Field(min_length=8, max_length=200)
    user_id: str = Field(min_length=1, max_length=100)
    lease_id: str | None = Field(default=None, max_length=100)
    connection_id: str | None = Field(default=None, max_length=200)
    target_host: str | None = Field(default=None, max_length=255)
    target_port: int | None = Field(default=None, ge=1, le=65535)
    upload_bytes: int = Field(default=0, ge=0, le=10_000_000_000_000)
    download_bytes: int = Field(default=0, ge=0, le=10_000_000_000_000)


def public_user(row: sqlite3.Row) -> dict[str, str]:
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "created_at": row["created_at"],
        "account_expires_at": account_expiry_date(row["account_expires_at"]),
    }


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


def current_metering_agent(
    token: Annotated[str | None, Header(alias="X-Metering-Token")] = None,
) -> None:
    if not METERING_TOKEN or not token or not secrets.compare_digest(token, METERING_TOKEN):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="计量服务未授权")


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


def _github_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "GoYou-Management-API",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="暂时无法读取版本发布信息") from error


def _github_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "GoYou-Management-API",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read().decode("utf-8").strip()
    except (OSError, urllib.error.URLError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="暂时无法读取版本签名") from error


def _update_platforms(release: dict) -> dict[str, dict[str, str]]:
    assets = release.get("assets") or []
    by_name = {asset.get("name"): asset for asset in assets if asset.get("name")}
    platforms: dict[str, dict[str, str]] = {}
    for name, asset in by_name.items():
        if name.endswith(".nsis.zip") or name.endswith("-setup.exe"):
            platform = "windows-x86_64"
        elif name.endswith(".app.tar.gz"):
            platform = "darwin-aarch64" if "aarch64" in name or "arm64" in name else "darwin-x86_64"
        else:
            continue
        signature_asset = by_name.get(f"{name}.sig")
        if not signature_asset:
            continue
        # The browser download URL redirects through release-assets and can
        # time out on restricted servers. GitHub's API asset URL returns the
        # same public bytes with Accept: application/octet-stream.
        signature_url = signature_asset.get("url") or signature_asset.get("browser_download_url")
        artifact_url = asset.get("browser_download_url")
        if not signature_url or not artifact_url:
            continue
        platforms[platform] = {
            "signature": _github_text(signature_url),
            "url": artifact_url,
        }
    return platforms


def _update_platform_key(target: str | None, arch: str | None) -> str | None:
    target_value = (target or "").strip().lower()
    arch_value = (arch or "").strip().lower()
    if target_value == "windows":
        return "windows-x86_64" if arch_value in {"x86_64", "x64", "amd64"} else None
    if target_value in {"darwin", "macos"}:
        if arch_value in {"aarch64", "arm64"}:
            return "darwin-aarch64"
        if arch_value in {"x86_64", "x64", "amd64"}:
            return "darwin-x86_64"
    return None


def _github_manifest(release: dict, requested_platform: str | None = None) -> dict | None:
    """Read the release's generated Tauri manifest in one request.

    Reading every signature asset through the GitHub API is unnecessarily
    slow on some networks and can exceed the desktop updater's timeout. The
    release workflow already publishes ``latest.json`` containing the same
    signed platform entries, so prefer that single asset and keep the older
    per-signature path as a compatibility fallback.
    """
    assets = release.get("assets") or []
    manifest_asset = next((asset for asset in assets if asset.get("name") == "latest.json"), None)
    manifest_url = manifest_asset.get("url") if manifest_asset else None
    if not manifest_url:
        return None
    try:
        manifest = json.loads(_github_text(manifest_url))
    except (HTTPException, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(manifest, dict):
        return None
    source_platforms = manifest.get("platforms")
    if not isinstance(source_platforms, dict):
        return None
    platforms = {
        platform: source_platforms[platform]
        for platform in UPDATE_PLATFORMS
        if isinstance(source_platforms.get(platform), dict)
        and source_platforms[platform].get("signature")
        and source_platforms[platform].get("url")
    }
    if not platforms or (requested_platform and requested_platform not in platforms):
        return None
    return {
        "version": str(manifest.get("version") or release.get("tag_name", "")).removeprefix("v"),
        "notes": manifest.get("notes") or release.get("body") or "GoYou 新版本",
        "pub_date": manifest.get("pub_date") or release.get("published_at") or release.get("created_at"),
        "platforms": platforms,
    }


def _version_key(version: str) -> tuple:
    """Sort semantic versions without adding another runtime dependency."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?$", version)
    if not match:
        return (0, 0, 0, 0, version)
    prerelease = match.group(4)
    # Stable releases sort after prereleases of the same numeric version.
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), 1 if not prerelease else 0, prerelease or "")


def _release_payload(row: sqlite3.Row, assets: list[sqlite3.Row] | list[dict], *, base_url: str) -> dict:
    platforms = {}
    for asset in assets:
        platform = asset["platform"] if isinstance(asset, sqlite3.Row) else asset.get("platform")
        platforms[platform] = {
            "url": f"{base_url}/v1/app/update/assets/{row['version']}/{platform}",
            "signature": asset["signature"] if isinstance(asset, sqlite3.Row) else asset.get("signature", ""),
        }
    return {
        "id": row["id"],
        "version": row["version"],
        "notes": row["notes"],
        "status": row["status"],
        "download_status": row["download_status"] or "manual",
        "download_platform": row["download_platform"],
        "download_error": row["download_error"],
        "download_started_at": row["download_started_at"],
        "download_finished_at": row["download_finished_at"],
        "created_at": row["created_at"],
        "published_at": row["published_at"],
        "platforms": platforms,
        "assets": [
            {
                "platform": asset["platform"] if isinstance(asset, sqlite3.Row) else asset.get("platform"),
                "filename": asset["filename"] if isinstance(asset, sqlite3.Row) else asset.get("filename"),
                "size_bytes": int(asset["size_bytes"] if isinstance(asset, sqlite3.Row) else asset.get("size_bytes", 0)),
                "sha256": asset["sha256"] if isinstance(asset, sqlite3.Row) else asset.get("sha256"),
                "created_at": asset["created_at"] if isinstance(asset, sqlite3.Row) else asset.get("created_at"),
            }
            for asset in assets
        ],
    }


def _release_rows(connection: sqlite3.Connection, *, status_filter: str | None = None) -> list[dict]:
    query = "SELECT * FROM app_releases"
    params: tuple = ()
    if status_filter:
        query += " WHERE status = ?"
        params = (status_filter,)
    rows = connection.execute(query, params).fetchall()
    result = []
    for row in rows:
        assets = connection.execute(
            "SELECT platform, filename, storage_path, signature, size_bytes, sha256, created_at FROM app_release_assets WHERE release_id = ? ORDER BY platform",
            (row["id"],),
        ).fetchall()
        result.append(_release_payload(row, assets, base_url=UPDATE_PUBLIC_BASE_URL))
    return result


@app.get("/v1/app/update/latest", response_model=None)
def latest_app_update(
    target: str | None = Query(default=None),
    arch: str | None = Query(default=None),
    current_version: str | None = Query(default=None),
) -> dict | Response:
    """Return the signed Tauri updater manifest managed by the admin console."""
    del current_version
    requested_platform = _update_platform_key(target, arch)
    with db() as connection:
        rows = connection.execute("SELECT * FROM app_releases WHERE status = 'published'").fetchall()
        candidates = []
        for row in rows:
            assets = connection.execute("SELECT * FROM app_release_assets WHERE release_id = ?", (row["id"],)).fetchall()
            platforms = {asset["platform"] for asset in assets}
            if all(platform in platforms for platform in UPDATE_PLATFORMS):
                candidates.append((row, assets))
        if candidates:
            row, assets = max(candidates, key=lambda item: _version_key(item[0]["version"]))
            payload = _release_payload(row, assets, base_url=UPDATE_PUBLIC_BASE_URL)
            return {"version": payload["version"], "notes": payload["notes"], "pub_date": payload["published_at"] or payload["created_at"], "platforms": payload["platforms"]}

    # Tauri treats 204 as the standard, successful "没有更新" response.
    # Returning JSON 404 here makes the client显示底层的 release JSON 错误。
    del requested_platform
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/v1/app/update/assets/{version}/{platform}")
def download_update_asset(version: str, platform: str) -> FileResponse:
    if platform not in UPDATE_PLATFORMS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="更新平台不存在")
    with db() as connection:
        asset = connection.execute(
            """SELECT a.storage_path, a.filename FROM app_release_assets a
               JOIN app_releases r ON r.id = a.release_id
               WHERE r.version = ? AND r.status = 'published' AND a.platform = ?""",
            (version, platform),
        ).fetchone()
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="更新文件不存在")
    path = Path(asset["storage_path"]).resolve()
    storage_root = UPDATE_STORAGE_DIR.resolve()
    if storage_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="更新文件不存在")
    return FileResponse(path, filename=asset["filename"], media_type="application/octet-stream")


@app.post("/v1/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest) -> dict:
    email = str(payload.email).lower()
    name = payload.name.strip() or email.split("@", 1)[0]
    user_id = secrets.token_hex(16)
    created_at = iso(now())
    try:
        with db() as connection:
            connection.execute(
                "INSERT INTO users(id, email, name, password_hash, created_at, account_expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, email, name, password_hasher.hash(payload.password), created_at, default_account_expires_at(datetime.fromisoformat(created_at))),
            )
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


@app.get("/v1/usage/today")
def today_usage(user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    payload = user_usage(user["id"])
    payload["account_expires_at"] = account_expiry_date(user["account_expires_at"])
    payload["account_expired"] = account_is_expired(user["account_expires_at"])
    return payload


@app.post("/v1/devices/register")
def register_device(payload: DeviceRequest, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    return {"device_id": secrets.token_hex(16), "name": payload.name, "platform": payload.platform, "user_id": user["id"]}


@app.post("/v1/proxy/lease")
def create_lease(payload: LeaseRequest, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    if account_is_expired(user["account_expires_at"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账户已到期，不可继续使用代理。")
    lease_id = secrets.token_hex(16)
    expires = now() + timedelta(hours=1)
    username, password = new_relay_credentials(lease_id)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        relay_port = allocate_relay_port(connection)
        connection.execute(
            "INSERT INTO leases(id, user_id, device_id, username, password, relay_port, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (lease_id, user["id"], payload.device_id, username, password, relay_port, iso(expires)),
        )
    return {
        "lease_id": lease_id,
        "host": RELAY_HOST,
        "port": relay_port,
        "method": RELAY_METHOD,
        "username": username,
        "password": password,
        "expires_at": iso(expires),
        "quota_exceeded": user_usage(user["id"])["exceeded"],
    }


@app.post("/v1/proxy/lease/refresh")
def refresh_lease(payload: LeaseRefreshRequest, user: Annotated[sqlite3.Row, Depends(current_user)]) -> dict:
    if account_is_expired(user["account_expires_at"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账户已到期，不可继续使用代理。")
    expires = now() + timedelta(hours=1)
    with db() as connection:
        connection.execute("BEGIN IMMEDIATE")
        lease = connection.execute("SELECT id, device_id, username, password, relay_port, revoked_at FROM leases WHERE id = ? AND user_id = ?", (payload.lease_id, user["id"])).fetchone()
        if not lease or lease["revoked_at"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="租约不存在或已撤销")
        username = lease["username"]
        password = lease["password"]
        relay_port = lease["relay_port"]
        if username == "relay" or password == RELAY_PASSWORD or relay_port is None:
            username, password = new_relay_credentials(payload.lease_id)
            relay_port = allocate_relay_port(connection)
        connection.execute(
            "UPDATE leases SET expires_at = ?, username = ?, password = ?, relay_port = ? WHERE id = ?",
            (iso(expires), username, password, relay_port, payload.lease_id),
        )
    return {
        "lease_id": payload.lease_id,
        "device_id": lease["device_id"],
        "host": RELAY_HOST,
        "port": relay_port,
        "method": RELAY_METHOD,
        "username": username,
        "password": password,
        "expires_at": iso(expires),
        "quota_exceeded": user_usage(user["id"])["exceeded"],
    }


@app.post("/v1/internal/usage/report")
def report_usage(
    payload: UsageReportRequest,
    _agent: Annotated[None, Depends(current_metering_agent)],
) -> dict:
    with db() as connection:
        user = connection.execute("SELECT id, disabled FROM users WHERE id = ?", (payload.user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        if user["disabled"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户已停用")
        lease_device_id = None
        if payload.lease_id:
            lease = connection.execute(
                "SELECT user_id, device_id FROM leases WHERE id = ? AND revoked_at IS NULL",
                (payload.lease_id,),
            ).fetchone()
            if not lease or lease["user_id"] != payload.user_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="租约与用户不匹配")
            lease_device_id = lease["device_id"]
        if payload.upload_bytes == 0 and payload.download_bytes == 0:
            return usage_payload(connection, payload.user_id)
        report = connection.execute(
            """INSERT OR IGNORE INTO usage_reports(
                   report_id, user_id, lease_id, device_id, connection_id,
                   target_host, target_port, upload_bytes, download_bytes, reported_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.report_id,
                payload.user_id,
                payload.lease_id,
                lease_device_id,
                payload.connection_id,
                payload.target_host.strip() if payload.target_host else None,
                payload.target_port,
                payload.upload_bytes,
                payload.download_bytes,
                iso(now()),
            ),
        )
        if report.rowcount:
            date = usage_date()
            current = connection.execute(
                "SELECT upload_bytes, download_bytes, total_bytes, exceeded_at FROM daily_usage WHERE user_id = ? AND usage_date = ?",
                (payload.user_id, date),
            ).fetchone()
            upload = (int(current["upload_bytes"]) if current else 0) + payload.upload_bytes
            download = (int(current["download_bytes"]) if current else 0) + payload.download_bytes
            total = upload + download
            quota = ensure_user_quota(connection, payload.user_id)
            exceeded_at = current["exceeded_at"] if current else None
            if exceeded_at is None and total >= int(quota["daily_limit_bytes"]):
                exceeded_at = iso(now())
            connection.execute(
                """INSERT INTO daily_usage(user_id, usage_date, upload_bytes, download_bytes, total_bytes, last_reported_at, exceeded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, usage_date) DO UPDATE SET upload_bytes = excluded.upload_bytes,
                   download_bytes = excluded.download_bytes, total_bytes = excluded.total_bytes,
                   last_reported_at = excluded.last_reported_at, exceeded_at = excluded.exceeded_at""",
                (payload.user_id, date, upload, download, total, iso(now()), exceeded_at),
            )
        return usage_payload(connection, payload.user_id)


@app.get("/v1/internal/relay/leases")
def relay_leases(
    _agent: Annotated[None, Depends(current_metering_agent)],
) -> dict:
    with db() as connection:
        rows = connection.execute(
            """SELECT l.id, l.user_id, l.username, l.password, l.relay_port, l.expires_at,
                      u.email, q.daily_limit_bytes
               FROM leases l JOIN users u ON u.id = l.user_id
               LEFT JOIN user_quotas q ON q.user_id = l.user_id
               LEFT JOIN daily_usage d ON d.user_id = l.user_id AND d.usage_date = ?
               WHERE l.revoked_at IS NULL AND l.expires_at > ? AND u.disabled = 0
                 AND (u.account_expires_at IS NULL OR u.account_expires_at > ?)
                 AND l.relay_port IS NOT NULL AND l.username != 'relay'
                 AND (d.total_bytes IS NULL OR d.total_bytes < COALESCE(q.daily_limit_bytes, ?))""",
            (usage_date(), iso(now()), iso(now()), DEFAULT_DAILY_QUOTA_BYTES),
        ).fetchall()
    return {
        "generated_at": iso(now()),
        "host": RELAY_HOST,
        "method": RELAY_METHOD,
        "leases": [
            {
                "lease_id": row["id"],
                "user_id": row["user_id"],
                "email": row["email"],
                "username": row["username"],
                "password": row["password"],
                "port": row["relay_port"],
                "expires_at": row["expires_at"],
                "quota_bytes": int(row["daily_limit_bytes"] or DEFAULT_DAILY_QUOTA_BYTES),
            }
            for row in rows
        ],
    }


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


def _release_or_404(connection: sqlite3.Connection, release_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM app_releases WHERE id = ?", (release_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="版本不存在")
    return row


def _asset_filename(platform: str, filename: str | None) -> str:
    name = Path(filename or "").name
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请上传有效的更新文件")
    if platform == "windows-x86_64" and not (name.endswith("-setup.exe") or name.endswith(".nsis.zip")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Windows 更新包必须是 -setup.exe 文件")
    if platform.startswith("darwin-") and not name.endswith(".app.tar.gz"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="macOS 更新包必须是 .app.tar.gz 文件")
    return name


async def _store_upload(upload: UploadFile, destination: Path) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.uploading")
    total = 0
    digest = hashlib.sha256()
    try:
        with temporary.open("wb") as handle:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > UPDATE_MAX_ASSET_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="更新文件超过大小限制")
                digest.update(chunk)
                handle.write(chunk)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return total, digest.hexdigest()


def _download_github_asset(asset: dict, destination: Path) -> tuple[int, str]:
    """从 GitHub API 下载资产到后台本地存储。"""
    asset_url = asset.get("url")
    if not asset_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="GitHub 更新资产地址无效")
    request = urllib.request.Request(
        asset_url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "GoYou-Management-API",
        },
    )
    temporary = destination.with_name(f".{destination.name}.downloading")
    total = 0
    digest = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > UPDATE_MAX_ASSET_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="GitHub 更新文件超过大小限制")
                digest.update(chunk)
                handle.write(chunk)
        temporary.replace(destination)
    except HTTPException:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError) as error:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="暂时无法下载 GitHub 更新文件") from error
    return total, digest.hexdigest()


@app.get("/v1/admin/releases")
def admin_releases(admin: Annotated[dict[str, str], Depends(current_admin)]) -> dict:
    with db() as connection:
        items = _release_rows(connection)
    items.sort(key=lambda item: (_version_key(item["version"]), item["created_at"]), reverse=True)
    return {"items": items}


@app.post("/v1/admin/releases", status_code=status.HTTP_201_CREATED)
def admin_create_release(
    payload: AdminReleaseCreateRequest,
    admin: Annotated[dict[str, str], Depends(current_admin)],
) -> dict:
    version = payload.version.removeprefix("v")
    release_id = secrets.token_hex(16)
    created_at = iso(now())
    try:
        with db() as connection:
            connection.execute(
                "INSERT INTO app_releases(id, version, notes, status, created_at) VALUES (?, ?, ?, 'draft', ?)",
                (release_id, version, payload.notes.strip(), created_at),
            )
            row = connection.execute("SELECT * FROM app_releases WHERE id = ?", (release_id,)).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该版本已经存在") from None
    return _release_payload(row, [], base_url=UPDATE_PUBLIC_BASE_URL)


def _download_imported_release_assets(release_id: str, selected: dict[str, tuple[dict, dict]]) -> None:
    """在后台线程下载 GitHub 资产，避免阻塞登录和其他 API 请求。"""
    try:
        started_at = iso(now())
        with db() as connection:
            connection.execute(
                """UPDATE app_releases
                   SET download_status = 'downloading', download_platform = NULL,
                       download_error = NULL, download_started_at = ?, download_finished_at = NULL
                   WHERE id = ? AND status = 'draft'""",
                (started_at, release_id),
            )
        for platform, (artifact, signature) in selected.items():
            with db() as connection:
                connection.execute(
                    "UPDATE app_releases SET download_platform = ? WHERE id = ? AND status = 'draft'",
                    (platform, release_id),
                )
            artifact_name = _asset_filename(platform, artifact.get("name"))
            signature_text = _github_text(signature.get("url", ""))
            if not signature_text:
                raise RuntimeError(f"{platform}签名文件为空")
            destination = UPDATE_STORAGE_DIR.resolve() / release_id / platform / artifact_name
            size_bytes, sha256 = _download_github_asset(artifact, destination)
            with db() as connection:
                connection.execute(
                    """INSERT INTO app_release_assets(release_id, platform, filename, storage_path, signature, size_bytes, sha256, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (release_id, platform, artifact_name, str(destination), signature_text, size_bytes, sha256, iso(now())),
                )
        with db() as connection:
            connection.execute(
                """UPDATE app_releases
                   SET download_status = 'completed', download_platform = NULL,
                       download_error = NULL, download_finished_at = ?
                   WHERE id = ? AND status = 'draft'""",
                (iso(now()), release_id),
            )
    except Exception as error:
        if isinstance(error, HTTPException):
            error_message = str(error.detail)
        else:
            error_message = f"{type(error).__name__}: {error}".strip()
        error_message = error_message[:1000]
        logger.exception("版本 %s 自动下载失败: %s", release_id, error_message)
        with db() as connection:
            connection.execute(
                """UPDATE app_releases
                   SET download_status = 'failed', download_platform = NULL,
                       download_error = ?, download_finished_at = ?
                   WHERE id = ? AND status = 'draft'""",
                (error_message or "后台下载失败，请手动上传更新文件", iso(now()), release_id),
            )


@app.post("/v1/admin/releases/import-github", status_code=status.HTTP_202_ACCEPTED)
def admin_import_github_release(
    payload: AdminReleaseImportRequest,
    background_tasks: BackgroundTasks,
    admin: Annotated[dict[str, str], Depends(current_admin)],
) -> dict:
    """将 GitHub 最新 Release 一键复制到后台，生成待审核草稿。"""
    del admin
    if not UPDATE_GITHUB_REPOSITORY or "/" not in UPDATE_GITHUB_REPOSITORY:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="尚未配置 GitHub 仓库")
    release = _github_json(f"https://api.github.com/repos/{UPDATE_GITHUB_REPOSITORY}/releases/latest")
    version = str(release.get("tag_name", "")).removeprefix("v")
    if not re.match(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$", version):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="GitHub Release 版本号无效")
    assets = {asset.get("name"): asset for asset in release.get("assets") or [] if asset.get("name")}
    selected: dict[str, tuple[dict, dict]] = {}
    for platform in UPDATE_PLATFORMS:
        if platform == "windows-x86_64":
            artifact = next((asset for name, asset in assets.items() if name.endswith("-setup.exe") or name.endswith(".nsis.zip")), None)
        elif platform == "darwin-aarch64":
            artifact = next((asset for name, asset in assets.items() if name.endswith(".app.tar.gz") and ("aarch64" in name or "arm64" in name)), None)
        else:
            artifact = next((asset for name, asset in assets.items() if name.endswith(".app.tar.gz") and "aarch64" not in name and "arm64" not in name), None)
        signature = assets.get(f"{artifact.get('name')}.sig") if artifact else None
        if not artifact or not signature:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"GitHub Release 缺少{platform}更新包或签名")
        selected[platform] = (artifact, signature)

    release_id = secrets.token_hex(16)
    created_at = iso(now())
    notes = payload.notes.strip() if payload.notes is not None else (release.get("body") or "GoYou 新版本").strip()
    try:
        with db() as connection:
            connection.execute(
                """INSERT INTO app_releases(
                       id, version, notes, status, download_status, download_started_at, created_at
                   ) VALUES (?, ?, ?, 'draft', 'downloading', ?, ?)""",
                (release_id, version, notes, created_at, created_at),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该版本已经存在，请先处理已有草稿") from None
    with db() as connection:
        row = connection.execute("SELECT * FROM app_releases WHERE id = ?", (release_id,)).fetchone()
    background_tasks.add_task(_download_imported_release_assets, release_id, selected)
    return _release_payload(row, [], base_url=UPDATE_PUBLIC_BASE_URL)


@app.patch("/v1/admin/releases/{release_id}")
def admin_update_release(
    release_id: str,
    payload: AdminReleaseUpdateRequest,
    admin: Annotated[dict[str, str], Depends(current_admin)],
) -> dict:
    with db() as connection:
        row = _release_or_404(connection, release_id)
        if row["status"] != "draft":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已发布版本不能修改，请先撤回")
        connection.execute("UPDATE app_releases SET notes = ? WHERE id = ?", (payload.notes.strip(), release_id))
        row = connection.execute("SELECT * FROM app_releases WHERE id = ?", (release_id,)).fetchone()
        assets = connection.execute("SELECT * FROM app_release_assets WHERE release_id = ?", (release_id,)).fetchall()
    return _release_payload(row, assets, base_url=UPDATE_PUBLIC_BASE_URL)


@app.post("/v1/admin/releases/{release_id}/assets/{platform}")
async def admin_upload_release_asset(
    release_id: str,
    platform: str,
    artifact: Annotated[UploadFile, File(...)],
    signature: Annotated[UploadFile, File(...)],
    admin: Annotated[dict[str, str], Depends(current_admin)],
) -> dict:
    if platform not in UPDATE_PLATFORMS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的更新平台")
    artifact_name = _asset_filename(platform, artifact.filename)
    signature_name = Path(signature.filename or "").name
    if not signature_name.endswith(".sig"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="签名文件必须是 .sig 文件")
    with db() as connection:
        release = _release_or_404(connection, release_id)
        if release["status"] != "draft":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已发布版本不能替换文件，请先撤回")
        if release["download_status"] == "downloading":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="自动下载进行中，请等待下载结束后再手动上传")
        old_asset = connection.execute("SELECT storage_path FROM app_release_assets WHERE release_id = ? AND platform = ?", (release_id, platform)).fetchone()
    destination = UPDATE_STORAGE_DIR.resolve() / release_id / platform / artifact_name
    try:
        signature_bytes = await signature.read(1024 * 1024 + 1)
        if len(signature_bytes) > 1024 * 1024:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="签名文件超过大小限制")
        signature_text = signature_bytes.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="签名文件不是有效文本") from None
    finally:
        await signature.close()
    if not signature_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="签名文件不能为空")
    size_bytes, sha256 = await _store_upload(artifact, destination)
    try:
        with db() as connection:
            current_release = _release_or_404(connection, release_id)
            if current_release["status"] != "draft":
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已发布版本不能替换文件，请先撤回")
            if current_release["download_status"] == "downloading":
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="自动下载进行中，请等待下载结束后再手动上传")
            connection.execute(
                """INSERT INTO app_release_assets(release_id, platform, filename, storage_path, signature, size_bytes, sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(release_id, platform) DO UPDATE SET filename = excluded.filename,
                   storage_path = excluded.storage_path, signature = excluded.signature,
                   size_bytes = excluded.size_bytes, sha256 = excluded.sha256, created_at = excluded.created_at""",
                (release_id, platform, artifact_name, str(destination), signature_text, size_bytes, sha256, iso(now())),
            )
            connection.execute(
                """UPDATE app_releases
                   SET download_status = CASE WHEN download_status = 'failed' THEN 'manual' ELSE download_status END,
                       download_error = CASE WHEN download_status = 'failed' THEN NULL ELSE download_error END
                   WHERE id = ?""",
                (release_id,),
            )
            row = connection.execute("SELECT * FROM app_releases WHERE id = ?", (release_id,)).fetchone()
            assets = connection.execute("SELECT * FROM app_release_assets WHERE release_id = ?", (release_id,)).fetchall()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if old_asset and old_asset["storage_path"] != str(destination):
        Path(old_asset["storage_path"]).unlink(missing_ok=True)
    return _release_payload(row, assets, base_url=UPDATE_PUBLIC_BASE_URL)


@app.post("/v1/admin/releases/{release_id}/publish")
def admin_publish_release(release_id: str, admin: Annotated[dict[str, str], Depends(current_admin)]) -> dict:
    with db() as connection:
        release = _release_or_404(connection, release_id)
        if release["download_status"] == "downloading":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="自动下载进行中，请等待下载结束后再发布")
        assets = connection.execute("SELECT * FROM app_release_assets WHERE release_id = ?", (release_id,)).fetchall()
        missing = [
            platform for platform in UPDATE_PLATFORMS
            if not any(asset["platform"] == platform and Path(asset["storage_path"]).is_file() for asset in assets)
        ]
        if missing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"请先上传更新包：{', '.join(missing)}")
        published_at = iso(now())
        connection.execute("UPDATE app_releases SET status = 'published', published_at = ? WHERE id = ?", (published_at, release_id))
        row = connection.execute("SELECT * FROM app_releases WHERE id = ?", (release_id,)).fetchone()
    return _release_payload(row, assets, base_url=UPDATE_PUBLIC_BASE_URL)


@app.post("/v1/admin/releases/{release_id}/unpublish")
def admin_unpublish_release(release_id: str, admin: Annotated[dict[str, str], Depends(current_admin)]) -> dict:
    with db() as connection:
        _release_or_404(connection, release_id)
        connection.execute("UPDATE app_releases SET status = 'draft', published_at = NULL WHERE id = ?", (release_id,))
        row = connection.execute("SELECT * FROM app_releases WHERE id = ?", (release_id,)).fetchone()
        assets = connection.execute("SELECT * FROM app_release_assets WHERE release_id = ?", (release_id,)).fetchall()
    return _release_payload(row, assets, base_url=UPDATE_PUBLIC_BASE_URL)


@app.delete("/v1/admin/releases/{release_id}")
def admin_delete_release(release_id: str, admin: Annotated[dict[str, str], Depends(current_admin)]) -> None:
    with db() as connection:
        release = _release_or_404(connection, release_id)
        if release["status"] != "draft":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已发布版本不能删除，请先撤回")
        if release["download_status"] == "downloading":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="自动下载进行中，请等待下载结束后再删除")
        paths = connection.execute("SELECT storage_path FROM app_release_assets WHERE release_id = ?", (release_id,)).fetchall()
        connection.execute("DELETE FROM app_releases WHERE id = ?", (release_id,))
    for path in paths:
        Path(path["storage_path"]).unlink(missing_ok=True)
    release_dir = UPDATE_STORAGE_DIR.resolve() / release_id
    if release_dir.exists():
        try:
            release_dir.rmdir()
        except OSError:
            pass
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
        "service": {
            "name": APP_NAME,
            "status": "ok",
            "relay_host": RELAY_HOST,
            "relay_port": RELAY_PORT,
            "relay_port_start": RELAY_PORT_START,
            "relay_port_end": RELAY_PORT_END,
            "relay_method": RELAY_METHOD,
        },
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
            f"""SELECT u.id, u.email, u.name, u.created_at, u.disabled, u.account_expires_at, COUNT(l.id) AS lease_count
                FROM users u LEFT JOIN leases l ON l.user_id = u.id
                WHERE {where}
                GROUP BY u.id ORDER BY u.created_at DESC LIMIT ? OFFSET ?""",
            [*params, page_size, offset],
        ).fetchall()
    items = []
    for row in rows:
        usage = user_usage(row["id"])
        items.append(
            {
                "id": row["id"],
                "email": row["email"],
                "name": row["name"],
                "created_at": row["created_at"],
                "account_expires_at": account_expiry_date(row["account_expires_at"]),
                "enabled": not bool(row["disabled"]),
                "lease_count": row["lease_count"],
                "daily_quota_bytes": usage["quota_bytes"],
                "used_bytes": usage["used_bytes"],
                "quota_exceeded": usage["exceeded"],
            }
        )
    return {
        "items": items,
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@app.post("/v1/admin/users", status_code=status.HTTP_201_CREATED)
def admin_create_user(
    payload: AdminCreateUserRequest,
    admin: Annotated[dict[str, str], Depends(current_admin)],
) -> dict:
    email = str(payload.email).lower()
    name = payload.name.strip() or email.split("@", 1)[0]
    user_id = secrets.token_hex(16)
    created_at = iso(now())
    expiry = account_expiry_value(payload.account_expires_at)
    try:
        with db() as connection:
            connection.execute(
                "INSERT INTO users(id, email, name, password_hash, created_at, account_expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, email, name, password_hasher.hash(payload.password), created_at, expiry),
            )
            ensure_user_quota(connection, user_id)
            connection.execute(
                "UPDATE user_quotas SET daily_limit_bytes = ?, updated_at = ? WHERE user_id = ?",
                (payload.daily_limit_bytes, iso(now()), user_id),
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该邮箱已经注册") from None
    usage = user_usage(user_id)
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "created_at": row["created_at"],
        "account_expires_at": account_expiry_date(row["account_expires_at"]),
        "enabled": True,
        "daily_quota_bytes": usage["quota_bytes"],
    }


@app.patch("/v1/admin/users/{user_id}/status")
def admin_user_status(user_id: str, payload: AdminUserStatusRequest, admin: Annotated[dict[str, str], Depends(current_admin)]) -> dict:
    with db() as connection:
        cursor = connection.execute("UPDATE users SET disabled = ? WHERE id = ?", (0 if payload.enabled else 1, user_id))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        row = connection.execute("SELECT id, email, name, created_at, disabled, account_expires_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"id": row["id"], "email": row["email"], "name": row["name"], "created_at": row["created_at"], "account_expires_at": account_expiry_date(row["account_expires_at"]), "enabled": not bool(row["disabled"])}


@app.patch("/v1/admin/users/{user_id}/expiry")
def admin_user_expiry(
    user_id: str,
    payload: AdminAccountExpiryRequest,
    admin: Annotated[dict[str, str], Depends(current_admin)],
) -> dict:
    with db() as connection:
        user = connection.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        expiry = account_expiry_value(payload.account_expires_at)
        connection.execute("UPDATE users SET account_expires_at = ? WHERE id = ?", (expiry, user_id))
        row = connection.execute("SELECT account_expires_at FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"user_id": user_id, "account_expires_at": account_expiry_date(row["account_expires_at"])}


@app.patch("/v1/admin/users/{user_id}/quota")
def admin_user_quota(
    user_id: str,
    payload: AdminQuotaRequest,
    admin: Annotated[dict[str, str], Depends(current_admin)],
) -> dict:
    with db() as connection:
        user = connection.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        connection.execute(
            """INSERT INTO user_quotas(user_id, daily_limit_bytes, timezone, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET daily_limit_bytes = excluded.daily_limit_bytes,
               updated_at = excluded.updated_at""",
            (user_id, payload.daily_limit_bytes, QUOTA_TIMEZONE, iso(now())),
        )
        connection.execute(
            """UPDATE daily_usage SET exceeded_at = COALESCE(exceeded_at, ?)
               WHERE user_id = ? AND usage_date = ? AND total_bytes >= ?""",
            (iso(now()), user_id, usage_date(), payload.daily_limit_bytes),
        )
    return user_usage(user_id)


@app.get("/v1/admin/users/{user_id}/usage")
def admin_user_usage(
    user_id: str,
    admin: Annotated[dict[str, str], Depends(current_admin)],
    days: int = Query(default=30, ge=1, le=366),
) -> dict:
    with db() as connection:
        user = connection.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
        quota = ensure_user_quota(connection, user_id)
        rows = connection.execute(
            "SELECT usage_date, upload_bytes, download_bytes, total_bytes, last_reported_at, exceeded_at FROM daily_usage WHERE user_id = ? ORDER BY usage_date DESC LIMIT ?",
            (user_id, days),
        ).fetchall()
    return {
        "quota_bytes": int(quota["daily_limit_bytes"]),
        "timezone": quota["timezone"],
        "today": user_usage(user_id),
        "items": [dict(row) for row in rows],
    }


@app.get("/v1/admin/usage")
def admin_usage(
    admin: Annotated[dict[str, str], Depends(current_admin)],
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    target_date = date or usage_date()
    offset = (page - 1) * page_size
    with db() as connection:
        total = connection.execute("SELECT COUNT(*) FROM daily_usage WHERE usage_date = ?", (target_date,)).fetchone()[0]
        rows = connection.execute(
            """SELECT d.user_id, u.email, u.name, d.usage_date, d.upload_bytes, d.download_bytes,
                      d.total_bytes, d.last_reported_at, d.exceeded_at, q.daily_limit_bytes
               FROM daily_usage d JOIN users u ON u.id = d.user_id
               JOIN user_quotas q ON q.user_id = d.user_id
               WHERE d.usage_date = ? ORDER BY d.total_bytes DESC LIMIT ? OFFSET ?""",
            (target_date, page_size, offset),
        ).fetchall()
    return {
        "date": target_date,
        "items": [dict(row) for row in rows],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


@app.get("/v1/admin/traffic-logs")
def admin_traffic_logs(
    admin: Annotated[dict[str, str], Depends(current_admin)],
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user_id: str | None = Query(default=None, max_length=100),
    lease_id: str | None = Query(default=None, max_length=100),
    keyword: str = Query(default="", max_length=255),
) -> dict:
    target_date = date or usage_date()
    start_at, end_at = usage_day_bounds(target_date)
    conditions = ["r.reported_at >= ?", "r.reported_at < ?"]
    params: list[str | int] = [start_at, end_at]
    if user_id:
        conditions.append("r.user_id = ?")
        params.append(user_id)
    if lease_id:
        conditions.append("r.lease_id = ?")
        params.append(lease_id)
    if keyword.strip():
        conditions.append(
            "(u.email LIKE ? OR u.name LIKE ? OR r.lease_id LIKE ? OR "
            "r.connection_id LIKE ? OR COALESCE(r.device_id, '') LIKE ? OR "
            "COALESCE(r.target_host, '') LIKE ?)"
        )
        term = f"%{keyword.strip()}%"
        params.extend([term, term, term, term, term, term])
    where = " AND ".join(conditions)
    offset = (page - 1) * page_size
    with db() as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM usage_reports r JOIN users u ON u.id = r.user_id WHERE {where}",
            params,
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT r.report_id, r.user_id, u.email, u.name, r.lease_id, r.device_id, r.connection_id,
                       r.target_host, r.target_port, r.upload_bytes, r.download_bytes,
                       (r.upload_bytes + r.download_bytes) AS total_bytes, r.reported_at
                FROM usage_reports r JOIN users u ON u.id = r.user_id
                WHERE {where}
                ORDER BY r.reported_at DESC LIMIT ? OFFSET ?""",
            [*params, page_size, offset],
        ).fetchall()
    return {
        "date": target_date,
        "items": [dict(row) for row in rows],
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


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
