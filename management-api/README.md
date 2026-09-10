# GoYou 管理 API

这是中转代理架构的第一版控制面，提供用户认证、设备注册、短期代理租约和管理员后台接口。

## 本机部署

```bash
cp .env.example .env
openssl rand -hex 32
docker compose up -d --build
curl http://127.0.0.1:18080/healthz
```

默认 API 端口为 `18080`，数据库持久化在 `./data/proxyswitch.sqlite3`（沿用已有部署数据路径）。

## 当前接口

- `POST /v1/auth/register`
- `POST /v1/auth/login`
- `POST /v1/auth/refresh`
- `POST /v1/auth/logout`
- `GET /v1/me`
- `GET /v1/usage/today`
- `POST /v1/devices/register`
- `POST /v1/proxy/lease`
- `POST /v1/proxy/lease/refresh`
- `POST /v1/admin/auth/login`
- `GET /v1/admin/auth/me`
- `GET /v1/admin/overview`
- `GET /v1/admin/users`
- `PATCH /v1/admin/users/{user_id}/status`
- `GET /v1/admin/leases`
- `POST /v1/admin/leases/{lease_id}/revoke`
- `PATCH /v1/admin/users/{user_id}/quota`
- `GET /v1/admin/users/{user_id}/usage`
- `GET /v1/admin/usage`
- `POST /v1/internal/usage/report` (trusted relay metering adapter)
- `GET /v1/internal/relay/leases` (trusted relay configuration sync)
- `GET /healthz`

管理员账号由 `ADMIN_EMAIL` 与 `ADMIN_PASSWORD_HASH`（推荐）或 `ADMIN_PASSWORD` 配置，不与客户端普通用户账号共用令牌。管理员令牌的 JWT 类型独立为 `admin_access`，不能调用客户端接口。

客户端生产 API 地址记录在本机 `docs/敏感信息.md`。构建客户端时可通过 `VITE_API_BASE_URL` 覆盖默认地址。

新租约会分配独立 Shadowsocks 凭据和 relay 端口（默认 `30000-39999`），计量适配器通过内部同步接口读取这些租约并生成 relay 配置。旧租约会在下一次刷新时迁移到独立凭据。

每日流量额度默认由 `DEFAULT_DAILY_QUOTA_BYTES` 设置（默认 500 MB），统计时区由 `QUOTA_TIMEZONE` 设置（默认 `Asia/Shanghai`）。`METERING_TOKEN` 只用于可信 relay 计量组件向内部接口上报上传和下载字节数；当前共享 relay 凭据尚不能区分用户，正式启用服务端限额前必须完成独立用户凭据和 relay 计量接入。

管理后台前端位于仓库 `admin/`，生产地址记录在本机 `docs/敏感信息.md`。静态文件由管理服务器 Nginx 提供，`/v1/*` 仍然反代到本 API 容器。
