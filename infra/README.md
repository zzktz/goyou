# sing-box 中转部署文件

## 当前拓扑

- `relay/` 可部署在管理服务器或独立 relay 服务器；每台节点通过 `RELAY_ID`、`RELAY_TOKEN` 绑定到管理后台
- `egress/` 部署在真实代理服务器 `出口服务器地址（见 docs/敏感信息.md）`
- `tunnel/` 部署在真实代理服务器，连接管理服务器 `12581` 端口
- relay 对外提供按租约分配的 Shadowsocks 端口（默认 `30000-39999`）；多节点可使用相同端口号，唯一性由节点 ID 隔离
- egress 只在本机监听 SOCKS `127.0.0.1:1080`
- SSH 反向隧道在管理服务器本机提供 `127.0.0.1:19080`
- `github-proxy/` 在管理服务器本机提供 `127.0.0.1:18081` HTTP CONNECT 代理，专门供管理 API 下载 GitHub 发布文件

## DNS 和防火墙

每台 relay 的域名应解析到对应节点，并在其云安全组放行配置的租约端口范围（默认 `30000-39999/tcp`）。真实出口服务器不需要开放 `1080/tcp`。

## 重要限制

relay 计量适配器从管理 API 同步每条租约的独立凭据和端口，动态生成 sing-box 配置，并通过 Clash API 读取连接计数上报用户流量。旧的 `24443` 固定凭据仅作为迁移兼容，不应继续分发给新客户端。

首次部署新 relay 时，在管理后台“Relay 管理”创建节点并复制一次性 Token，然后在该节点的 `.env` 中设置：

```text
MANAGEMENT_API_URL=https://管理服务器地址
RELAY_ID=relay-xxxxxxxx
RELAY_TOKEN=后台生成的 Token
```

旧的单 relay 部署可继续使用 `RELAY_ID=default` 和共享 `METERING_TOKEN`，迁移完成后建议为默认节点重新生成独立 Token。

## GitHub 下载代理

管理 API 使用 Python `urllib` 下载 GitHub 发布文件，不能直接使用 `socks5h://127.0.0.1:19080`。因此单独运行 `github-proxy/`，将本机 HTTP 代理转发到现有 SSH 隧道，再在管理 API 的服务器 `.env` 中设置：

```text
HTTP_PROXY=http://127.0.0.1:18081
HTTPS_PROXY=http://127.0.0.1:18081
NO_PROXY=127.0.0.1,localhost,proxy.123371.com
```

该代理只监听回环地址，不应开放防火墙端口，也不要修改 relay 自动生成的 `relay.json`。
