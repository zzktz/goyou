# GoYou 基础分流规则

这些 `.srs` 文件由 GoYou 客户端随 sing-box 一起打包，用于实现“国内直连、国外代理”：

- `geosite-cn.srs`：中国大陆域名集合，匹配后使用 `direct`。
- `geoip-cn.srs`：中国大陆 IPv4 网段集合，匹配后使用 `direct`。
- 未匹配的公网目标使用 `relay`，因此不需要额外打包完整的国外规则集合。

来源：

- <https://github.com/SagerNet/sing-geosite/tree/rule-set>
- <https://github.com/SagerNet/sing-geoip/tree/rule-set>

当前文件校验值（SHA-256）：

```text
geoip-cn.srs    ebee603fdf402314b44b9f653cdcf6d9cc9c41e84e2b3515e3123c5c920a93bc
geosite-cn.srs  a32d727f1a71b2f9c67627ea4ae489d8d6cfb010f8ec2d6800538493fe5a77ae
```

规则属于可更新数据，不应在客户端逻辑中硬编码具体域名或网段。后续更新规则时重新生成/替换这两个文件并发布客户端，或接入带校验的远程缓存更新机制。
