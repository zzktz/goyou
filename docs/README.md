# GoYou 文档索引

## 先读这些

1. [项目说明](./项目说明.md)：项目边界、目录和运行入口。
2. [中转代理部署进度](./中转代理部署进度.md)：服务器、域名、隧道和恢复命令；远程操作前优先阅读。
3. [中转代理架构方案](./中转代理架构方案.md)：流量路径、组件职责和安全边界。
4. [代理功能设计](./代理功能设计.md)：客户端状态、租约和兼容模式。
5. [开发交接](./开发交接.md)：当前代码结构、验证命令和下一步工作。

## 可视化

- [系统拓扑图](./系统拓扑图.html)：可在浏览器中打开，查看控制面、数据面、中转和出口链路。

## 子项目文档

- [管理后台](../admin/README.md)：Vue 3 + Ant Design Vue；生产地址保存在本机的 `docs/敏感信息.md`，不提交到 Git。
- [管理 API](../management-api/README.md)：FastAPI、认证、管理员接口和 Docker 运行方式。
- [基础设施](../infra/README.md)：relay、egress 和 SSH 反向隧道配置。

## 目录约定

- `docs/`：当前仍有效的简体中文文档。
- `admin/`：管理服务器后台前端。
- `management-api/`：管理 API 服务。
- `infra/`：服务器部署模板，不包含真实密码和私钥。
- `temp/`：历史资料和可重复生成的构建产物，不参与构建和部署。

## 常用验证

```bash
pnpm typecheck
pnpm build:renderer
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
PYTHONPYCACHEPREFIX=/tmp/goyou-pycache python3 -m py_compile management-api/app/main.py
npm run build --prefix admin
```

桌面开发启动可以使用 PATH 中的 `sing-box`；正式发行包会在构建时自动下载并内置对应平台的 sidecar：

```bash
GOYOU_SING_BOX_PATH="$PWD/.tools/sing-box/sing-box" pnpm tauri dev
```

正式用户不需要安装 sing-box。发布构建会将 macOS、Windows 或 Linux 对应的 sing-box 放入 GoYou 资源目录，由应用自动启动。
