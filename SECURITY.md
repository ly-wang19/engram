# 安全策略 / Security Policy

## 支持版本

当前接受安全修复的版本：

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |
| < 0.1.0 | No |

0.1.0 的商业交付边界是单节点自托管。TLS、互联网边界防护、分布式限流、企业 SSO/RBAC、
密钥管理服务和监管认证不由默认进程自动提供，生产环境应把 Engram 放在可信反向代理或 API 网关之后。

## 报告漏洞

请不要先在公开 Issue 中披露可利用细节、真实密钥或用户数据。请通过仓库的
[GitHub 私密漏洞报告](https://github.com/ly-wang19/engram/security/advisories/new)提交；商业客户也可使用合同约定的私密支持渠道。

请包含受影响版本、最小复现、影响范围和建议修复。不要附带真实客户数据；请使用合成样例。
维护者会尽快确认收到，并在完成影响评估后协调修复与披露时间。商业支持响应时间以单独合同为准，
开源仓库不自动承诺 SLA、赔偿或合规结论。

## 生产安全基线

- 必须设置 `ENGRAM_API_KEYS`，不要在生产启用 `ENGRAM_OPEN` 或 `ENGRAM_ALLOW_ANONYMOUS`。
- 使用至少 32 个随机字符的密钥，通过部署平台的 secret 功能注入，不要提交 `.env`。
- 服务默认只绑定本机地址；由 TLS 反向代理对外提供访问、速率限制和访问日志脱敏。
- 为 `/data` 配置受限权限、加密磁盘、定期备份和恢复演练。
- 默认安全导出不包含敏感事实和原始对话；完整私有导出只能在用户明确授权时执行。
- 轮换密钥时先为同一租户加入新密钥，切换客户端后再移除旧密钥。

## 设计保证与限制

0.1.0 对逻辑租户 ID 使用带摘要的安全目录名，防止目录穿越和字符过滤碰撞；整租户删除只允许作用于
数据根目录的已验证子项。静态密钥配置适合单节点自托管，不等同于完整身份平台。更多边界见
[`docs/commercial-release-0.1.0.zh-CN.md`](docs/commercial-release-0.1.0.zh-CN.md)。

---

Security reports should use the repository's
[private GitHub advisory form](https://github.com/ly-wang19/engram/security/advisories/new). Do not include
production secrets or customer data. Version 0.1.x is supported; production deployments must use
configured API keys and place Engram behind a TLS reverse proxy or API gateway.
