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

SQLite 数据库默认使用 WAL 模式和 5 秒 busy timeout。`usage_reports` 连接级流量明细默认保留 90 天，后台进程每天执行一次清理并使用 SQLite 在线备份 API 生成一致性备份；`daily_usage` 汇总数据不会被清理。备份默认保存在数据库目录下的 `backups/`，保留 7 天，可通过 `USAGE_REPORT_RETENTION_DAYS`、`DB_BACKUP_DIR`、`DB_BACKUP_INTERVAL_SECONDS`、`DB_BACKUP_RETENTION_DAYS` 和 `DB_BUSY_TIMEOUT_MS` 调整。

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
- `GET /v1/admin/relays`
- `POST /v1/admin/relays`
- `PATCH /v1/admin/relays/{relay_id}`
- `DELETE /v1/admin/relays/{relay_id}`
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
- `GET /v1/admin/releases`（支持 `page`、`page_size` 分页）
- `POST /v1/admin/releases`
- `PATCH /v1/admin/releases/{release_id}`
- `POST /v1/admin/releases/{release_id}/assets/{platform}` (multipart: `artifact` + `signature`)
- `POST /v1/admin/releases/{release_id}/publish`
- `POST /v1/admin/releases/{release_id}/unpublish`
- `DELETE /v1/admin/releases/{release_id}`

版本删除接口可删除草稿或已发布版本（下载中的版本除外），并会同时清理该版本的数据库记录、所有平台更新包、签名文件及版本存储目录。

管理员账号由 `ADMIN_EMAIL` 与 `ADMIN_PASSWORD_HASH`（推荐）或 `ADMIN_PASSWORD` 配置，不与客户端普通用户账号共用令牌。管理员令牌的 JWT 类型独立为 `admin_access`，不能调用客户端接口。

运行总览中的月度流量按 `QUOTA_TIMEZONE` 统计，每个周期从当月 10 日 00:00 到下月 10 日 00:00，固定额度为 1000 GB，并汇总所有用户的上传和下载用量。

用户自助注册必须先调用 `/v1/auth/register/send-code` 获取邮箱验证码，再提交 `/v1/auth/register`。注册接口只接受邮箱格式账号，验证码有效期 10 分钟，单个邮箱每 60 秒最多发送一次。生产环境需要配置 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD` 和 `SMTP_FROM`；使用 465 端口的 SSL 邮箱时设置 `SMTP_USE_SSL=1`，否则默认使用 STARTTLS。

忘记密码时先调用 `/v1/auth/password-reset/send-code`，再提交 `/v1/auth/password-reset` 设置新密码。密码重置成功后，该用户已有的刷新令牌会全部失效。

客户端生产 API 地址记录在本机 `docs/敏感信息.md`。构建客户端时可通过 `VITE_API_BASE_URL` 覆盖默认地址。

新租约会按活跃租约数与权重分配到一个已启用且未排空的 relay，并分配该 relay 端口范围内的独立 Shadowsocks 凭据和端口（默认 `30000-39999`）。计量适配器通过内部同步接口只读取自己 `RELAY_ID` 对应的租约并生成 relay 配置。旧租约会在下一次刷新时迁移到独立凭据；当原 relay 被停用或排空时，刷新会自动迁移到其他可用 relay。

管理后台“Relay 管理”支持新增、编辑、启用/停用、排空和删除节点。创建节点后只显示一次 Agent Token，请写入对应 relay 的 `RELAY_ID`、`RELAY_TOKEN`；删除仅允许没有历史租约的节点，已有租约应先停用或排空并等待过期。旧部署启动时会自动创建 `default` relay，并把历史租约和旧 `relay_health` 记录迁移到该节点；原有 `METERING_TOKEN` 仍兼容默认节点，新增节点推荐使用独立 Token。

多 relay 部署示例：每台 relay 使用相同的 `MANAGEMENT_API_URL`，但设置不同的 `RELAY_ID` 和后台生成的 `RELAY_TOKEN`，并为各节点配置不同的公网主机地址及端口范围。端口唯一性按 `(relay_id, relay_port)` 约束，因此不同节点可以复用相同的端口号。

relay 计量适配器会定期调用心跳接口；`RELAY_HEARTBEAT_TIMEOUT_SECONDS`（默认 `30` 秒）用于判断后台总览中的心跳是否超时。

每日流量额度默认由 `DEFAULT_DAILY_QUOTA_BYTES` 设置（默认 1000 MB），统计时区由 `QUOTA_TIMEZONE` 设置（默认 `Asia/Shanghai`）。每条租约使用独立凭据，relay 计量组件通过内部接口上报上传和下载字节数；`METERING_TOKEN` 仅作为旧版默认 relay 的兼容认证配置，新节点应使用后台生成的独立 Token。

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
