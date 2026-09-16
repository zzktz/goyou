# 多 Relay 管理开发总结

## 目标

将管理服务器与 relay 解耦，支持后台独立管理多个 relay 节点。客户端申请租约后获得具体 relay 地址和端口，relay Agent 只同步属于自己的租约。

## 主要改动

- 管理 API 新增 `relays` 表，记录节点地址、端口范围、加密方式、权重、启用/排空状态和 Token 哈希。
- `leases` 增加 `relay_id`，端口唯一性调整为 `(relay_id, relay_port)`。
- 新租约按“活跃租约数 ÷ 权重”选择节点；停用或排空节点不再接收新租约。
- 刷新租约时保留原节点；原节点不可用时自动迁移到可用节点。
- relay 心跳、租约同步、流量上报按 Relay ID 和独立 Token 隔离。
- 管理后台新增 Relay 管理页面，支持新增、编辑、启用/停用、排空、删除和 Token 重新生成。
- 总览接口保留旧的 `relay` 字段，同时新增 `relays` 节点列表，兼容原有页面。

## 兼容与迁移

旧数据库启动时会自动：

1. 创建 `default` relay，配置取自 `RELAY_HOST`、`RELAY_PORT`、`RELAY_PORT_START`、`RELAY_PORT_END` 等旧环境变量。
2. 将历史租约归属 `default`。
3. 将旧的单行 `relay_health` 迁移为按 `relay_id` 存储。
4. 保留旧共享 `METERING_TOKEN` 对默认 relay 的认证兼容。

## 部署步骤

1. 发布管理 API 后打开后台“Relay 管理”，确认自动生成的 `default` 节点。
2. 新增 relay，保存主机地址、端口范围、加密方式和权重，并复制一次性 Token。
3. 在对应 relay Agent 的环境中设置：

   ```dotenv
   MANAGEMENT_API_URL=https://管理服务器地址
   RELAY_ID=relay-xxxxxxxx
   RELAY_TOKEN=后台生成的一次性 Token
   ```

4. 启动 Agent，确认后台节点健康状态、心跳和监听端口正常。
5. 需要下线节点时先点击“排空”，等待租约过期或刷新迁移，再停用或删除（有历史租约的节点不可删除）。

## API

```text
GET    /v1/admin/relays
POST   /v1/admin/relays
PATCH  /v1/admin/relays/{relay_id}
DELETE /v1/admin/relays/{relay_id}
```

Agent 请求必须携带 `X-Relay-ID` 和 `X-Metering-Token`。旧版默认 Agent 可以暂时只携带共享 `METERING_TOKEN`。
