# GoYou 管理后台

这是管理服务器的 Vue 3 + Ant Design Vue 后台，沿用 `/Users/houshangjun/game/spine/admin` 的侧边布局、登录卡片、表格筛选和分页模式。

## 本地开发

```bash
npm install
VITE_API_BASE_URL=<管理 API 地址> npm run dev
```

开发服务器默认地址为 `http://localhost:4173/admin/`。不设置 `VITE_API_BASE_URL` 时，Vite 会把 `/v1` 和 `/healthz` 代理到本机 `127.0.0.1:18080`。

## 构建

```bash
npm run build
```

生产环境使用 `/admin/` 路径部署，API 地址默认取当前访问域名。管理员账号由管理 API 的 `ADMIN_EMAIL` 与 `ADMIN_PASSWORD_HASH`（推荐）或 `ADMIN_PASSWORD` 环境变量提供。

## 页面

- 运行总览：API、用户和租约统计，以及 relay 连接信息
- 用户管理：搜索用户、查看租约数、启用或停用账号
- 代理租约：按用户/设备查询租约，撤销活跃租约
- 版本发布：创建版本草稿、上传三平台 updater 包及签名、发布或撤回版本
- 系统设置：查看当前控制面连接和认证策略
