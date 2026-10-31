# 问答证据与选择性遗忘（2026-10-30）

契约 B 专项。唯一协调事实源为 HANDOFF.md。本域实现与合成数据测试已完成；根会话统一负责共享接口、两份架构文档、完整测试、构建和浏览器验收。没有提交、部署、调用外部模型或访问真实数据。

## 验收与当前结果

1. 回答从同一份带 ID 上下文生成，逐陈述引用可定位事实/原文和纠错；敏感与 as_of 边界通过。
2. 预览令牌绑定租户、精确范围、数据版本和有效期；显式确认、变动拒绝、到期和单次使用通过。
3. 原文及依赖事实、冷热层、图、摘要、工作记忆、画像缓存、问题/体检处理状态、接入事件及提炼任务清理通过；其他原文、独立事实和其他租户保留。
4. 合成数据回归 **36 passed**，本域 Ruff 通过；前端 TypeScript 检查通过。浏览器和全量回归由根会话统一执行，专项未声称它们已通过。

## 最终 API

### 证据问答

`EvidenceAnswers(service)`，独立域，不改变 `Memory.search`、`lean_context`、`MemoryService.recall` 或 harness 算法。

- `POST /v1/ask`：`query`、`n_chunks`（1–12，默认6）、`as_of`（可选 epoch 秒）、`redact_sensitive`（默认true）。
- 返回 `answer_status`、`answer`、`claims[{text,evidence_ids}]`、`citations`、`context_evidence`、`context`、`tokens_est`、时间与敏感设置。
- 状态为 `completed / abstained / unavailable / error / invalid_citations / stale_evidence / no_evidence`。未配置模型/模型失败/无有效引用不伪造答案。
- 候选证据先编号 `E1...`，实际送入模型的 JSON 上下文和响应中的文本完全一致。回答模型只调用一次；模型返回的每条陈述必须引用上下文内的有效 ID。`citations` 仅含模型选择的引用，`context_evidence` 明确为全部候选。
- 总文本预算24000字符；单事实最多2000、单原文最多6000字符。引用标注 `truncated`，并可打开完整出处。不存在另检索一组证据给旧答案补引用的路径。
- `GET /v1/evidence/{token}?include_sensitive=false&offset=0&limit=20`：返回该事实及来源原文分页，或直接返回原文。引用有效期24小时、租户隔离、绑定原对象内容与来源指纹；对象编辑返回409，遗忘或引用失效返回404。
- 原文和敏感事实需要提问时关闭脱敏；打开出处时还需要显式 `include_sensitive=true`。脱敏提问生成的引用不能事后升级成原文通行证。as_of 同时过滤事实有效时间/创建时间，以及原文事件时间/入库时间。
- 模型调用结束后再次核验引用：即使模型失败或返回坏格式，期间发生编辑/遗忘也不会返还旧上下文。
- 前端 `Ask.tsx`：每条答案旁的编号和引用卡片可打开 `SourcePanel`；更正调用现有 `/v1/facts/{id}`，随后撤下旧答案并提示重新提问。查询缓存按账号、引用、敏感选项和页码隔离。

**证据界限**：这些检查证明“引用文本实际进入了这一轮生成，且模型引用了该 ID”，不是语义蕴含正确性的自动评审；没有运行付费模型效果评测。未改变原有纠错接口的原地编辑历史语义。

### 选择性遗忘

`SelectiveForgetting(service)`，每次预览/执行在 `service.write_lock(user)` 内工作。

- `POST /v1/forget/preview`：`{scope, include_sensitive:false}`。
- `scope` 支持 `session_ids`（最多100个精确 ID）、`source`（`Episode.metadata.source` 精确匹配）、`time_from`/`time_to`（须成对提供，有限值、开始小于结束）。各条件取交集，时间为 `[from,to)`。拒绝空条件、未知字段、半开无界时间和无匹配数据。仅时间范围也可选择无原文来源的手动事实。
- 返回随机 `token`、内容 `version`、规范化 `scope`、15分钟有效期、原文/事实前20条预览和完整影响计数。默认原文及敏感事实隐藏。
- `GET /v1/forget/preview/{token}?kind=episodes|facts&include_sensitive=false&offset=0&limit=20`：服务端分页，版本变化409。
- `POST /v1/forget/confirm`：必须携带原 `token / version / scope` 和严格布尔 `confirm:true`。跨租户、重用、到期、范围/内容变动返回409；未明确确认400。公共演示/匿名空间403。
- 前端 `SelectiveForget` 在 Privacy 页面提供范围表单、影响计数、原文/事实分页、到期提示、错误反馈和输入 `FORGET` 的确认。更改任何条件即撤下旧预览；成功后清除前端查询缓存。

### 派生清理策略

- 任一来源被遗忘，即整条删除依赖它的事实，包含共享来源事实和通过事实 ID 继续派生的传递依赖；不会删掉部分 provenance 后保留原来的复合结论。
- 无 provenance 的自动提炼事实无法证明与范围无关，纳入预览并删除；无来源手动事实仅在时间范围直接选中时删除。
- 删除原文 Doc/Vector、事实冷热层、关联图关系和孤立实体、关联冲突、受影响会话的工作记忆；清空瞬时工作集。
- `reflect()` 可把新事实复制到其他会话摘要，但现有摘要没有完整来源链。因此本租户**全部摘要**、画像/抽取器身份缓存重置；其他原文保留，以后可重新生成摘要。
- 问题/体检记录的备注和处理历史也缺少完整内容来源链，因此本租户这些记录一并重置，预览明确计数。配置、关注主题、全局模型配置不删除。
- 删除受影响会话的接入事件；重算对应来源的剩余事件聚合，没有剩余事件时删除聚合。同步事件只含状态和计数。
- `jobs.scrub` 在同一写锁内清理相关会话作业。真实已领取作业的延迟执行测试证实：释放后 `_job_guard` 拒绝回写，旧任务和接入事件不复活。
- 所有旧证据引用和预览令牌失效；保留导入内容哈希，以继续去重而不携带原文。

## 持久化与根会话整合钩子

路由暴露 `create_router(get_service, auth_dependency)`；挂载：

```python
app.include_router(evidence_routes.create_router(svc, auth))
app.include_router(forget_routes.create_router(svc, auth))
```

共享 `request` 已导出；前端域模块统一使用它，不另写认证 fetch。无需挂 `svc.evidence` 或 `svc.forgetting` 属性。

已对齐 A 的实际接口：`svc.jobs.scrub(user, session_ids=[...])`，由遗忘域在租户写锁内调用，不再二次加用户锁。根会话 `_close_session_impl(..., _job_guard=...)` 必须在同一锁内检查任务所有权；本次相关集成测试已通过。

**根会话已接入以下读取屏障**（加载前恢复与加载后清理两步均保留）：

```python
with self.write_lock(user):
    forgetting = SelectiveForgetting(self)
    forgetting.restore_pending_snapshot_locked(user)  # 在 _get_unchecked / Memory.open 之前
    mem = self._get_unchecked(user)
    forgetting.recover_locked(user, mem)
    return mem
```

原因：原 JSONL 快照是逐文件替换；中途失败可留下“新文件、旧 manifest 计数”，直接 `Memory.open` 会先失败，无法走到后置恢复。

遗忘域先保存无正文的 `forget_intents`（已批准的精确 ID 集），执行清理，然后在租户目录 `.forget-stage-<operation_id>` 生成**完整已清理快照**，最后才写正式快照。加载前钩子在待完成意图存在时从该完整快照恢复正式文件，manifest 最后替换；后置恢复重复同一组删除，持久化成功后标记完成并移除暂存。阶段快照中已经没有被遗忘的数据。保存失败返回503，不报告成功；读取恢复失败应继续阻止该租户读取。

`test_partial_live_snapshot_write_is_repaired_before_open` 人为中断正式快照的第2个集合写入，然后直接创建全新 `MemoryService` 并导出，不手动调用恢复方法；已验证实际读取屏障自动修复快照且不复活被遗忘内容。

此处为应用可召回数据的逻辑删除；不涉及部署系统外部备份和文件系统取证擦除。

## 验证

```bash
python3 -m pytest tests/test_evidence_api.py tests/test_selective_forget.py -q
# 36 passed
python3 -m ruff check engram/management/evidence.py engram/management/forgetting.py \
  engram/server/evidence_routes.py engram/server/forget_routes.py \
  tests/test_evidence_api.py tests/test_selective_forget.py
# All checks passed!
cd frontend
./node_modules/.bin/tsc --noEmit -p tsconfig.app.json
# exit 0
```

覆盖：实际模型输入与引用一致、拒绝未知/空/坏格式引用、敏感默认与显式原文访问、双时间过滤、同租户事实/原文定位、纠错使旧引用失效、回答期间修改/遗忘；范围交集/分页/确认/时效/复用/版本变化；共享来源与传递依赖、冷热/图/摘要/工作记忆/缓存/状态清理；接入事件重算；重启不复活；保存失败和逐文件写中断恢复；已领取提炼作业竞态。

## 架构文档需要合入的内容

- 产品问答支路：`Ask → /v1/ask → frozen ID evidence → one answer call → claim/citation validation → source readback/correction`；原 recall/harness 保持。
- 遗忘支路：`scope preview/version → explicit confirmation → durable intent → cancel jobs → cascade erase → sanitized stage snapshot → live save → management cleanup → completed`。
- 每个租户加载前恢复暂存快照、加载后执行待完成意图，再向所有调用面提供 Memory。
- 原文/敏感 opt-in、as_of、共享来源整条删除、无完整来源链摘要/处理记录的保守重置，及实测证据链接到上述两个测试文件。
