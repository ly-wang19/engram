# 模型与接入管理（2026-10-30）

本轮提供运行时模型配置、权限隔离、连接探测和服务端可观察的接入状态。界面位于设置页，领域实现为 `engram/management/models.py`、`connections.py`；不更换 embedding 或重新评分基准。

## 配置与访问

- 部署管理员在服务端设置 `ENGRAM_ADMIN_KEYS`（逗号分隔的独立管理员令牌）。业务 Bearer 身份仍必需；额外 `X-Engram-Admin-Key` 才能调用全局模型接口。普通租户不能提权。
- `GET /v1/admin/access` 只返回管理员功能是否启用和本请求是否为管理员。`GET /v1/admin/models` 返回三角色状态；`PUT /v1/admin/models/{extractor|answerer|vision}` 保存配置；`POST /v1/admin/models/{role}/test` 执行连接探测。
- 角色支持 `inherit`、`custom`、`offline`。自定义字段为模型标识、可选 HTTP(S) endpoint、provider key 和 1–120 秒超时。保存后本服务后续请求使用新对象，热内存的提炼器同步重建，摘要器 `summarizer.llm` 同步替换；数据、向量和事实保留。
- 配置持久于数据目录 `.management.sqlite3` 的全局空命名空间；文件权限 0600。凭证不进入 GET 响应，前端管理员令牌只保留在当前页面内存，不写 localStorage 或 Query key。模型密码保存后清空输入。
- endpoint 改动不会携带旧 provider key；key 留空在 endpoint 未变时保留，显式 `clear_key=true` 删除。配置失败回退上一配置；配置变化清除旧的连接成功记录。
- 探测仅发固定文本、最多一次尝试，最多 15 秒，最多 8 输出 tokens。返回成功、时间、延迟或安全异常类别；不回显 provider 异常原文。图像角色的测试明确只证明文本连接，图像能力仍须实际图像核验。
- 环境继承值仍取进程启动时环境。独立 MCP/多进程实例共享持久配置，但其他已运行进程需重启读取新配置；不宣称跨主机配置分发。

## 来源观察

- `GET /v1/connections` 返回每个来源的原文/会话数、最近入库与事件时间、观察状态、失败计数；`GET /v1/connections/events` 支持来源筛选及分页。
- `remember`、`import`、结论提炼记录实际成功/原文已保存/待提炼/失败。`POST /v1/connections/report` 可由已认证客户端上报同步状态，仅写入其命名空间；失败原因只接受异常类别。
- 历史原文可派生“已观察”状态；不能据此推断设备在线。到不了服务端且客户端未上报的失败不可见，UI 明示此边界。
- 每租户保留最近 500 条事件，无原文和模型凭证。遗忘在租户写锁内同步删除涉及会话的事件并重建来源汇总。整租户清除不删除全局模型配置。

## 验证

`python3 -m pytest tests/test_management_settings.py -q -o addopts=''`：8 项通过。包含 SQLite 并发更新/重开、模型配置应用/重开/凭证隐藏、权限拒绝、失败不误报成功、来源隔离/持久化/遗忘。真实 LiteLLM adapter 请求本地 HTTP provider，确认 header、模型与 token 参数、成功和 401 失败路径；未调用外部模型。有一条 LiteLLM/Pydantic 序列化兼容告警，不影响回执或断言。

统一 UI、全量回归和发布边界见 `product-completion-validation-20261030.md`。该功能已随 `product-20261030-d64e86214b92` 部署；部署时另补充已有热内存摘要器切换断言。真实线上原有DeepSeek凭证无效，需更新有效配置才能恢复提炼/生成，详见[发布回执](../deploy/releases/product-20261030-d64e86214b92/README.zh-CN.md)。
