# 提炼任务中心（契约 A，2026-10-30）

## 本域验收标准

1. 持久化 pending/running/succeeded/failed、原因和重试历史；仅 outcomes_status=completed 算提炼成功。
2. 同租户同会话运行中请求去重；重启后未完成工作可以恢复，执行中断不能被标成成功。
3. 同步 close_session 保留旧响应契约，异步 API 和会话/结论共用入口使用实际任务状态。
4. 合成数据覆盖隔离、去重、失败、重试、重启和同步兼容；本域测试和前端构建通过。

## 最终 API 与集成钩子

- `DistillationJobs(service)` 仅调用 `service._close_session_impl(...)` 执行，不调用 public close_session。
- 路由工厂 `create_router(get_service, auth_dependency)`；前端共用 `features/jobs/SessionDistillButton.tsx`。
- 状态数据使用契约 ManagementStore，不访问私人实例；共享文件与架构图由根会话统一集成。

### 根会话需加的执行守卫（遗忘竞态）

`_close_session_impl(..., *, _job_guard=None)`：进入既有 `with self.write_lock(user):` 后、读取或修改记忆前，执行 `if _job_guard is not None: _job_guard()`。
守卫失败抛出 `engram.management.jobs.JobCancelled`，由任务域捕获，不让旧任务继续；必须在该锁内检查以覆盖“已领取但尚未获得写锁”的窗口。
全量遗忘在同一租户写锁内调用 `svc.jobs.scrub(user)`；选择性遗忘在同一锁内调用 `svc.jobs.scrub(user, session_ids=[...受影响会话...])`。scrub 删除这些任务和历史，完成回写使用事务 CAS，不能复活被删除的记录。不要在遗忘锁内调用 jobs.run_sync。

### 后端

`DistillationJobs(service)` 需要 `service.data_dir`、`service.management_store` 及 `_close_session_impl`。

- `enqueue(user, session_id, summarize=True, clear_working=True, outcomes=None, *, start=True) -> job`：先持久化，再唤醒线程。同一租户、同一会话的 pending/running 请求复用任务；参数不一致返回冲突，避免吞掉请求语义。
- `list(user, *, status=None, session_id=None, limit=20, offset=0)`：筛选后服务端分页，返回 `{jobs, counts, page}`。
- `get(user, job_id) -> job`；其他租户不能按 ID 读取。
- `retry(user, job_id, *, start=True) -> job`：仅 failed 可重试，复用 ID，attempt 增加，保留 history。运行中的重复重试复用原尝试；成功任务重试返回冲突。
- `recover(user=None)`：使用 OS 文件锁确认是否还有进程持有执行权；孤立 running 转 failed/interrupted，绝不推断成功。pending 保留，由 start 自动继续。
- `start()`：幂等启动后台线程；服务启动时调用，可以恢复持久队列。线程通过 `ManagementStore.scan('distillation_jobs')` 内部发现租户，HTTP 不暴露跨租户扫描。
- `close(timeout=5.0)`：停止领取新任务，有界等待正在执行的任务；未完成不标成功。若进程随后终止，下一次 recover 会标中断。
- `run_sync(user, session_id, summarize=True, clear_working=True, outcomes=None)`：兼容原 close 参数，返回原回执字段并增加 job_id/job_status/job_reason；仅 completed 且 ok=true 标 succeeded。
- `cancel(user, session_ids=None)` / `scrub(user, session_ids=None)`：均返回影响任务数，空列表无操作；scrub 同时删除历史和回执。遗忘调用要求见上文执行守卫。

根会话：初始化 `self.jobs = DistillationJobs(self)`；公共 close_session 委托 `self.jobs.run_sync(...)`；原实现改名并加守卫；在服务启动/关闭挂 `jobs.start()` / `jobs.close()`。HTTP 挂 `job_routes.create_router(svc, auth)`。

若已挂 `service.connections`，执行完成会在租户写锁内写入 `distill` 来源观察，按实际 job 状态记录 succeeded/failed，只含来源、会话标识、计数与安全异常类别；连接观察失败不会篡改提炼回执。scrub 后的迟到完成不会补写来源记录。

HTTP：`GET /v1/jobs`（status/session_id/limit/offset），`POST /v1/jobs`（session_id、三个 close 选项，202），`GET /v1/jobs/{id}`，`POST /v1/jobs/{id}/retry`（202）；404 隔离不存在/异租户 ID，409 冲突，422 参数错误。

存储：每租户 `distillation_jobs/queue`，包含该租户任务与尝试历史；SQLite update 事务内执行去重、状态转换和终态 CAS。`.distillation-locks/<用户+会话 SHA256>.lock` 只含空锁文件，文件名不存原文或会话 ID。此锁不替代 MemoryService 的写锁；域层不会持有写锁后再调用 close 实现。

### 前端

- 默认导出 `frontend/src/features/jobs/JobsPage.tsx`，由根会话挂 `/jobs` 并加入导航。
- 命名/默认导出 `SessionDistillButton`，属性 `{sessionId: string, label?: ReactNode, className?: string, onComplete?: (job: DistillationJob) => void}`。onComplete 在本按钮发起的任务终结后触发，**成功和失败都会回调**；判断成功必须使用 `job.status === 'succeeded'`。
- Journal/C 可直接引入 `../features/jobs/SessionDistillButton`；Conversations 已替换同步关闭按钮，并在会话卡片/会话审阅弹窗增加入口。
- API 统一使用公共 request；所有查询缓存 key 含当前 apiKey 和筛选/分页条件；切换账号或会话会重建按钮状态；中英文、空/加载/错误/重试历史齐备。

## 验证（2026-10-30，本域）

- `python3 -m pytest tests/test_management_jobs.py -q`：23 项通过，exit 0。使用临时 SQLite、SyntheticService 和合成会话，未调用外部模型。
- `cd frontend && npm run build`：TypeScript + Vite 构建通过，exit 0。
- `python3 -m ruff check engram/management/jobs.py engram/server/job_routes.py tests/test_management_jobs.py`：通过。
- 覆盖：持久队列重启、筛选后分页、跨租户 ID 隔离、跨实例并发去重、真实文件锁活性、不同选项冲突、所有 outcomes_status 失败分支、重试历史、异常脱敏、同步加入异步任务、运行后迟到回执 CAS、遗忘锁前执行守卫、空范围与选择性清理、有界关闭不报成功。

## 尚待根会话集成验证的边界

- 本域测试使用 SyntheticService 验证已约定写锁内守卫；真实 MemoryService 的重命名/委托/守卫、服务生命周期、路由与导航挂载需根会话接入后统一验收。
- 根会话负责两份架构文档同步、完整回归及桌面/窄屏浏览器交互验收。本域不修改共享主文件、不提交、不部署、不访问真实数据。
- 同会话去重定义为 **pending/running 期间**；完成后的显式新提炼产生新任务，原结论去重仍由已有 close 实现负责。未宣称跨任务 exactly-once：若写入已落盘但终态回执前进程崩溃，任务显示 interrupted，由用户决定重试。
- 此实现面向现有单机 Linux/macOS；运行权使用 fcntl 文件锁，不宣称跨主机队列能力。
