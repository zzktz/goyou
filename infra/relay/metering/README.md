# Relay 计量适配器

`metering_adapter.py` 是 relay 侧的可信适配器，负责：

1. 使用 `GET /v1/internal/relay/leases` 同步有效租约；
2. 为每条租约生成独立 Shadowsocks 入站和端口配置；
3. 启用 sing-box Clash API，轮询连接上传/下载计数；
4. 将增量流量通过 `X-Metering-Token` 上报 `/v1/internal/usage/report`；
5. API 返回超额后关闭该连接，并在下一次配置同步时移除失效租约。

## 配置

```bash
export MANAGEMENT_API_URL=https://proxy.example.com
export METERING_TOKEN='server-only-token'
export RELAY_CONFIG_PATH=/var/lib/goyou/relay.json
export METERING_STATE_PATH=/var/lib/goyou/metering-state.json
export EGRESS_HOST=127.0.0.1
export EGRESS_PORT=19080
```

适配器需要与 `sing-box` 二进制位于同一 relay 主机，且管理 API 的 `METERING_TOKEN` 必须只配置在该主机，不能放入客户端或公开仓库。

## 注意

- 首次切换前应先确认 `30000-39999` 端口范围已在防火墙放行。
- 这是按连接轮询的计量适配器，轮询间隔由 `SYNC_INTERVAL_SECONDS` 设置。正式生产环境应继续增加断线重试、健康检查、告警和本地额度缓存。
- 旧固定凭据只用于迁移兼容，生成独立租约后应在完成验证后关闭旧端口。
