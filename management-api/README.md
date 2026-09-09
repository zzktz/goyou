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
- `GET /healthz`

管理员账号由 `ADMIN_EMAIL` 与 `ADMIN_PASSWORD_HASH`（推荐）或 `ADMIN_PASSWORD` 配置，不与客户端普通用户账号共用令牌。管理员令牌的 JWT 类型独立为 `admin_access`，不能调用客户端接口。

客户端生产 API 地址记录在本机 `docs/敏感信息.md`。构建客户端时可通过 `VITE_API_BASE_URL` 覆盖默认地址。

当前租约接口返回 relay 的固定测试凭据并写入控制面记录，尚未按用户生成独立 sing-box 入口凭据；下一步需要将租约创建/撤销同步到 relay 动态配置。

管理后台前端位于仓库 `admin/`，生产地址记录在本机 `docs/敏感信息.md`。静态文件由管理服务器 Nginx 提供，`/v1/*` 仍然反代到本 API 容器。
