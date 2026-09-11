# sing-box 中转部署文件

## 当前拓扑

- `relay/` 部署在管理服务器 `管理服务器地址（见 docs/敏感信息.md）`
- `egress/` 部署在真实代理服务器 `出口服务器地址（见 docs/敏感信息.md）`
- `tunnel/` 部署在真实代理服务器，连接管理服务器 `12581` 端口
- relay 对外提供按租约分配的 Shadowsocks 端口（默认 `30000-39999`）
- egress 只在本机监听 SOCKS `127.0.0.1:1080`
- SSH 反向隧道在管理服务器本机提供 `127.0.0.1:19080`
- `github-proxy/` 在管理服务器本机提供 `127.0.0.1:18081` HTTP CONNECT 代理，专门供管理 API 下载 GitHub 发布文件

## DNS 和防火墙

`relay 域名（见 docs/敏感信息.md）` 的 A 记录已经解析到 `管理服务器地址（见 docs/敏感信息.md）`。还需要在管理服务器云安全组放行 `30000-39999/tcp`；主机 firewalld 已放行。真实出口服务器不需要开放 `1080/tcp`。

## 重要限制

relay 计量适配器从管理 API 同步每条租约的独立凭据和端口，动态生成 sing-box 配置，并通过 Clash API 读取连接计数上报用户流量。旧的 `24443` 固定凭据仅作为迁移兼容，不应继续分发给新客户端。

## GitHub 下载代理

管理 API 使用 Python `urllib` 下载 GitHub 发布文件，不能直接使用 `socks5h://127.0.0.1:19080`。因此单独运行 `github-proxy/`，将本机 HTTP 代理转发到现有 SSH 隧道，再在管理 API 的服务器 `.env` 中设置：

```text
HTTP_PROXY=http://127.0.0.1:18081
HTTPS_PROXY=http://127.0.0.1:18081
NO_PROXY=127.0.0.1,localhost,proxy.123371.com
```

该代理只监听回环地址，不应开放防火墙端口，也不要修改 relay 自动生成的 `relay.json`。
