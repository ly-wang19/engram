# 六项产品能力协作契约（2026-10-30）

唯一协调源：HANDOFF.md 顶部。根会话负责共享文件集成、模型与接入、基础持久化、统一验收；各专项只写分配文件。

## 共享基础接口（主会话提供）

`engram.management.store.ManagementStore(data_dir)`，纯stdlib SQLite，路径 `<data_dir>/.management.sqlite3`，权限0600。方法：
- `get(namespace, collection, key) -> dict | None`
- `put(namespace, collection, key, value: dict) -> dict`（JSON值原样保存返回）
- `delete(namespace, collection, key) -> bool`
- `list(namespace, collection) -> list[dict]`（返回值，不包装，不分页；领域模块过滤排序分页）
- `clear_namespace(namespace)`（清除本租户管理状态）
- `update(namespace, collection, key, fn) -> dict | None`（SQLite事务内读-改-写，fn接收旧dict或None，返回新dict或None；None删除）

`svc().management_store` 可用。域类均接收 MemoryService，自行读写 store 并遵守 svc.write_lock(user) 的内存写入锁；不得在已持有write_lock时调用会再次加锁的service写方法。

路由文件暴露 `create_router(get_service, auth_dependency) -> APIRouter`，由主会话挂载。避免从 app 反向导入以防循环。路径 `/v1/...`。独立领域API模块使用公共 `request` 函数（主会话将从 frontend/src/lib/api.ts 导出），不在自己的fetch里重新实现认证或抛弃401行为。React Query key必须含 useAuth当前apiKey；敏感显示与分页条件进入key。提供双语、空/加载/失败/确认反馈，移动端不溢出。

## 专项边界

A 提炼：jobs.py、job_routes.py、frontend/src/features/jobs/*、pages/Conversations.tsx、tests/test_management_jobs.py。领域对象 DistillationJobs(service)；主会话将挂 `svc().jobs`，将原close_session改名 `_close_session_impl`，public close_session委托 jobs.run_sync；任务执行内部调用 `_close_session_impl` 避免递归。run_sync参数与原close一致。提供 enqueue/list/retry/recover/start/close 按实现文档说明。旧客户端兼容，异步任务页面可独立挂 `/jobs`。结论页补提炼入口由C使用你提供的 `SessionDistillButton`。不得独立修改 service/app/Journal。

B 证据与遗忘：management/evidence.py、management/forgetting.py、server/evidence_routes.py、server/forget_routes.py、features/evidence/*、features/forgetting/*、pages/Ask.tsx、pages/Privacy.tsx、tests/test_evidence_api.py、tests/test_selective_forget.py。证据必须对应实际送入回答的上下文，不能把第二次检索结果冒充引用；可提供独立 `/v1/ask` 保持原recall/harness不变。默认保持脱敏边界、as_of。遗忘需预览令牌/版本+再次确认、范围变化409、空或无界条件拒绝；处理raw/summary/facts冷热层/图/缓存/working/derived provenance，不能只删原文留下派生回忆；应说明共享来源事实的策略并验证。不得改memory.py或service.py；需要共享钩子时给根会话精确接口。

C 问题与体检：management/review.py、server/review_routes.py、features/review/*、pages/Journal.tsx、pages/Health.tsx、tests/test_review_queue.py。问题状态与解决结论关联持久化、同租户验证、可重开；真正服务端分页。体检全量findings分页、kind筛选、open/ignored/resolved，处理记录持久化，源变化不能无声沿用旧忽略。可调用svc.audit(user, limit=2147483647)取既有规则结果，但不改检索规则。结论页补提炼不能无条件成功，改用A的 SessionDistillButton；如接口未到先兼容 outcomes_status。

根会话：store.py、models.py、connections.py、admin_routes.py、connection_routes.py、Settings和ModelSettings组件、共享文件、构建/端到端与文档。管理员密钥由服务端明确配置，普通namespace不能提升权限；secret不回显、不写浏览器localStorage/工具输出。

## 完成回执

专项结束写自己的 `docs/product-<domain>-20261030.md`：最终API、root需要接的钩子、测试命令/结果和未完成事项。只宣称已验证范围。主会话会read/wait结果，不需要向其它聊天发消息。禁止再开子任务或审计；出现阻塞先留下可合并代码，继续本域独立可做部分。
