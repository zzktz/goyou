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
- `POST /v1/auth/register/send-code` (发送邮箱验证码)
- `POST /v1/auth/password-reset/send-code` (发送密码重置验证码)
- `POST /v1/auth/password-reset` (使用邮箱验证码重置密码)
- `GET /v1/auth/settings` (读取客户端认证设置)
- `POST /v1/auth/login`
- `POST /v1/auth/refresh`
- `POST /v1/auth/logout`
- `GET /v1/me`
- `GET /v1/usage/today`
- `GET /v1/feedback` (用户查看自己的反馈与回复)
- `POST /v1/feedback` (用户提交文字反馈和截图)
- `POST /v1/devices/register`
- `POST /v1/proxy/lease`
- `POST /v1/proxy/lease/refresh`
- `POST /v1/admin/auth/login`
- `GET /v1/admin/auth/me`
- `GET /v1/admin/profile`
- `PATCH /v1/admin/profile`
- `GET /v1/admin/settings`
- `PATCH /v1/admin/settings`
- `GET /v1/admin/overview`
- `GET /v1/admin/users`
- `POST /v1/admin/users`
- `PATCH /v1/admin/users/{user_id}/status`
- `GET /v1/admin/leases`
- `POST /v1/admin/leases/{lease_id}/revoke`
- `PATCH /v1/admin/users/{user_id}/quota`
- `GET /v1/admin/users/{user_id}/usage`
- `GET /v1/admin/usage`
- `POST /v1/internal/usage/report` (trusted relay metering adapter)
- `POST /v1/internal/relay/heartbeat` (trusted relay process and port health)
- `GET /v1/admin/traffic-logs` (管理员查看连接级流量、设备号、目标域名和端口)
- `GET /v1/admin/feedback`
- `POST /v1/admin/feedback/{feedback_id}/reply`
- `GET /v1/admin/feedback/{feedback_id}/attachments/{attachment_id}`
- `GET /v1/internal/relay/leases` (trusted relay configuration sync)
- `GET /healthz`
- `GET /v1/app/update/latest` (返回后台已发布的签名更新清单)
- `GET /v1/app/update/assets/{version}/{platform}` (下载已发布的更新包)
- `GET /v1/admin/releases`
- `POST /v1/admin/releases`
- `PATCH /v1/admin/releases/{release_id}`
- `POST /v1/admin/releases/{release_id}/assets/{platform}` (multipart: `artifact` + `signature`)
- `POST /v1/admin/releases/{release_id}/publish`
- `POST /v1/admin/releases/{release_id}/unpublish`
- `DELETE /v1/admin/releases/{release_id}`

管理员账号由 `ADMIN_EMAIL` 与 `ADMIN_PASSWORD_HASH`（推荐）或 `ADMIN_PASSWORD` 配置，不与客户端普通用户账号共用令牌。管理员令牌的 JWT 类型独立为 `admin_access`，不能调用客户端接口。

用户自助注册必须先调用 `/v1/auth/register/send-code` 获取邮箱验证码，再提交 `/v1/auth/register`。注册接口只接受邮箱格式账号，验证码有效期 10 分钟，单个邮箱每 60 秒最多发送一次。生产环境需要配置 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD` 和 `SMTP_FROM`；使用 465 端口的 SSL 邮箱时设置 `SMTP_USE_SSL=1`，否则默认使用 STARTTLS。

忘记密码时先调用 `/v1/auth/password-reset/send-code`，再提交 `/v1/auth/password-reset` 设置新密码。密码重置成功后，该用户已有的刷新令牌会全部失效。

客户端生产 API 地址记录在本机 `docs/敏感信息.md`。构建客户端时可通过 `VITE_API_BASE_URL` 覆盖默认地址。

新租约会分配独立 Shadowsocks 凭据和 relay 端口（默认 `30000-39999`），计量适配器通过内部同步接口读取这些租约并生成 relay 配置。旧租约会在下一次刷新时迁移到独立凭据。

relay 计量适配器会定期调用心跳接口；`RELAY_HEARTBEAT_TIMEOUT_SECONDS`（默认 `30` 秒）用于判断后台总览中的心跳是否超时。

每日流量额度默认由 `DEFAULT_DAILY_QUOTA_BYTES` 设置（默认 1000 MB），统计时区由 `QUOTA_TIMEZONE` 设置（默认 `Asia/Shanghai`）。`METERING_TOKEN` 只用于可信 relay 计量组件向内部接口上报上传和下载字节数；当前共享 relay 凭据尚不能区分用户，正式启用服务端限额前必须完成独立用户凭据和 relay 计量接入。

管理后台前端位于仓库 `admin/`，生产地址记录在本机 `docs/敏感信息.md`。静态文件由管理服务器 Nginx 提供，`/v1/*` 仍然反代到本 API 容器。

系统设置中的“新用户默认有效期”按注册当天加指定天数计算，只影响之后注册的用户；已有用户的到期时间不会自动变化。设置为 `0` 时，账号在注册当天结束时到期。

桌面端版本徽标支持手动检查更新。管理员在后台“版本发布”页面创建草稿，分别上传
Windows、macOS Apple 芯片和 macOS Intel 芯片的 updater 包及 `.sig` 签名文件，确认三个平台齐全后发布。
文件默认保存到 `UPDATE_STORAGE_DIR`（生产环境应挂载持久化磁盘），公开下载地址使用
`UPDATE_PUBLIC_BASE_URL`。客户端更新接口只读取后台已发布版本，不会自动读取 GitHub 或其他来源的版本。
管理员可在后台使用“一键创建”从配置的 GitHub Release 导入版本草稿，审核后再发布。
一键创建后，草稿会显示“下载中”“下载完成”或“下载失败”状态；下载失败的草稿会保留，管理员可以根据失败原因改为手动上传。下载进行中不允许手动上传或发布，后台页面会自动刷新下载状态。

客户端“问题反馈”支持文字和最多 3 张 PNG、JPEG 或 WebP 截图，每张不能超过 5 MiB。附件保存在
`FEEDBACK_STORAGE_DIR`（默认与数据库同级的 `feedback/` 目录），只能通过管理员已认证的附件接口读取。管理员回复后，用户可在客户端反馈窗口中查看回复。
由于截图以 JSON Base64 传输，请在生产 Nginx 的 `/v1/` 反代位置或 server 块设置
`client_max_body_size 25m;`，再执行 `nginx -t` 并重载 Nginx。
