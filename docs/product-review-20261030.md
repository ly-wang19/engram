# 未决问题与体检队列专项（2026-10-30）

当前阶段：专项实现与本地验证已完成；等待根会话挂载路由、更新共享缓存失效和统一浏览器验收。验收标准：
1. 未决问题 open/resolved、解决结论关联与重开跨重启保留；跨租户及过期来源写入拒绝。
2. 结论和问题的类型、搜索、状态筛选在服务端分页之前执行。
3. 体检遍历完整规则结果；kind/status 分页，忽略与处理历史落盘；来源变化重新待处理。
4. 合成数据边界测试和前端构建通过；本专项不部署、不使用真实记忆、不调用外部模型。

## 最终集成接口

- `engram.server.review_routes.create_router(get_service, auth_dependency)`，由根会话 include_router。
- 领域对象 `ReviewQueue(service)`，直接使用 `service.management_store`，不需要额外 service 属性。
- `GET /v1/review/outcomes`：kind=all/decision/finding/lesson/open_question/resolution，status=live/superseded/all，q、include_sensitive、limit、offset。
- `GET /v1/review/questions`：status=open/resolved/all，q、include_sensitive、limit、offset。
- `PATCH /v1/review/questions/{fact_id}`：status、resolution_id、note、expected_version、include_sensitive。
- `GET /v1/review/health`：kind、status=open/ignored/resolved/all，q、include_sensitive、limit、offset。
- `PATCH /v1/review/health/{finding_id}`：status、note、expected_version、include_sensitive。
- 管理集合：`review_questions`、`review_health`。选择性遗忘需清理对应记录/历史中的引用及备注；整租户遗忘由 ManagementStore.clear_namespace 处理。
- 共享 query invalidation 请包含 `['review', apiKey]`，以便其它页面改写/遗忘/提炼后更新本域缓存。
- Journal 已采用 A 的 `SessionDistillButton`，直接读取持久任务的 pending/running/succeeded/failed 状态；移除了旧 close 返回后无条件成功的提示。

## 接口语义

- 所有列表返回 `items` 和 `page={total,limit,offset,has_more,next_offset}`。limit 为 1–100，offset 非负。类型、搜索、状态、敏感过滤都在分页之前执行。
- 问题额外返回 `by_status`；每条有 `review_status`、`resolution_id`、`resolution`、`resolution_hidden`、`history`、`version`。
- 解决关联只接受本租户有效的 decision/finding/lesson，允许跨会话；不接受另一个 open_question、普通属性、已失效事实、其它租户事实。隐藏敏感结论需要 `include_sensitive=true` 才能关联/读取。
- 体检额外返回 `checked`、`total_findings`（仍被当前规则报告的数量）、`by_kind` 和 `by_status`。历史已不再被规则报告的条目保留为 resolved、present=false，只展示类型和处理历史，不保存原事实正文副本。
- PATCH 必须携带列表回读的 64 字符 `expected_version`；来源或处理版本已变返回 409，前端刷新本域查询。处理备注最长 2000 字符。无权限目标返回 404，无效解决关联返回 422。
- 来源版本包含事实内容、双时间字段、敏感标记及完整来源原文/摘要；槽位问题对所有成员取版本，不只比较五条 samples。访问次数、salience 等召回侧变化不会重开。
- 当前问题或关联解决结论发生修改/删除/失效时，问题重开并记录原因。体检来源变化或消失后重现时重开；来源不再被报告时自动记录 no_longer_flagged。显式人工 resolved 只修改处理状态，不改记忆内容。
- 所有操作按 namespace 隔离，管理状态读改写处于 `service.write_lock(user)` 内。GET 会协调派生处理状态，因此可以落盘 source_changed/no_longer_flagged 记录。
- 默认隐藏敏感事实与备注；仍可看处理时间、状态与原因。`include_sensitive=true` 才返回完整备注和历史关联 ID。页面提供对应开关，查询 key 包括 apiKey、筛选和敏感设置；账号切换会清空页面局部选择。

## 根会话需要的钩子

```python
from .review_routes import create_router as create_review_router
app.include_router(create_review_router(svc, auth))
```

无需在 MemoryService 创建 ReviewQueue 实例；router 按请求构造轻量领域对象。A 的任务回读已失效 `['review', apiKey]`；共享编辑/删除、遗忘及其它改写入口也需包含此 key。本专项页面自己的编辑/删除/清槽操作已显式刷新它。

选择性遗忘：`review_questions` 记录 key/id 是问题事实 ID，resolution_id 是解决事实 ID，history 也可能含该引用与人工备注；`review_health` 记录含 fact_ids（完整来源成员），history/note 是人工备注。删除涉及这些 ID 的整个管理记录是保守且不会保留引用/备注的清理办法。store 记录未复制原文与事实正文。根会话与 B 需把此范围纳入遗忘事务/清理钩子。

架构文档由根会话统一更新：新增 SQLite 管理状态旁路、GET 时状态协调、源版本乐观并发、完整 audit 扫描后的服务端筛选分页；未修改记忆检索/提炼算法与既有 audit 规则。

## 验证结果

- `python3 -m pytest tests/test_review_queue.py tests/test_console_browse.py tests/test_outcomes.py -o addopts='' -q`：**60 passed in 1.99s**（专项 17 项；其余为既有浏览与会话结论回归）。
- `npm run build --prefix frontend`：TypeScript 与 Vite 构建成功，1668 modules transformed。
- `git diff --check -- frontend/src/pages/Journal.tsx frontend/src/pages/Health.tsx`：通过。
- 测试使用临时目录、合成事实/原文、hashing embedder，无模型或外部服务；覆盖持久重启、租户与敏感隔离、完整 137 条队列、过滤前分页、来源变更、解决结论失效、隐藏的第六个槽位成员改变、处理版本冲突与 HTTP 验证。

## 完成边界

代码在共享工作树，未提交或部署；未接触真实记忆。浏览器桌面/窄屏操作、app 路由集成、全量离线测试和两份架构文档由主会话按总契约统一验收；本专项不以测试与 build 冒充整个产品已经验收。
