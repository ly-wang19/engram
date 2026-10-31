# 可靠性故障案例与核验边界（2026-10-30）

本文面向本地可靠性修复的实现者和核验者，把真实使用中出现的 `close_failed`、提炼失败和恢复问题转成六类合成案例。所有案例均可用临时目录、合成消息及离线替身执行，不需要真实私人库、远端服务或付费模型。

核心判断是：**原文已保存、提炼已完成、失败后仍可恢复，是三个分别需要证据的结果。** `ok=true`、HTTP 200、进程退出码 0 或 `outcomes=0`，都不能单独证明这三个结果同时成立。

本轮已完成一次独立核验：读取三条修复路径相对开工快照的 diff，检查固定回归，实际运行 **25 个固定用例，全部通过**。另外一个临时目录探针确认保存中断后恢复 `pending=1 / facts=0` 的旧完整状态，再次提炼和保存可完成。未发现阻断本轮三条修复路径的明确问题；完整回归、零配置检查和最终交付结论由统一验收记录承载。

## 已有证据及其适用范围

- **历史真实观察**：[真实使用脱敏记录](../results/real_usage_loop_2026-10-25.json)第 16–31、62–67 行记录了一次采集的 `episodes=1`、`outcomes=0`、`close_failed=2`、`error=null`，且提炼状态为 `error`、provider 凭证被拒绝。它支持“原文存取可用、提炼失败”，不证明当前运行态已恢复，也不证明六类案例都曾在线上发生。
- **历史合成验收**：[六项产品验收](product-completion-validation-20261030.md)明确使用隔离目录和可控模型，包含任务重启、重试与失败状态。它证明对应验收当时的行为，不替代本轮代码的回归或真实 provider 验收。
- **当前直接复现**：本文的“关闭回执探针”在改动前工作树运行；它通过内存替身调用 `watch.ingest`，没有网络请求。结果见案例 2。三条路径的失败对照与修复后日志均已读取，见各案例链接。
- **现有测试覆盖**：下文的原有测试名称已按源码核对；“有测试”表示已有断言，不表示本文重新执行了整套测试。本轮独立实际执行的是三个新增文件中的 25 个固定用例，命令及结果见末节。

代码位置以本轮开工阶段读取的源码为准；修改导致行号移动时，以路径与函数名定位。初始快照入口记录在项目 [HANDOFF.md](../HANDOFF.md)。

## 六类合成案例

### 1. Provider 异常和合法空结果必须可区分

**合成输入**：同一租户、同一合成会话，先令 `complete()` 抛出 `RuntimeError`，再替换为正常返回 `[]` 的模型。

**期望结果**：第一次保留原文，`outcomes_status=error`，任务失败且可重试；第二次为 `completed`、`outcomes=0`。错误回执只保留安全错误类型，不带模型请求、密钥或消息正文。

**已有依据与覆盖**：

- 历史真实观察直接支持这一故障类型。
- `engram/service.py:841–893` 的 `_close_session_impl` 区分 `disabled`、`no_episodes`、`unavailable`、`completed`、`error`，并在保存后返回状态。
- `engram/management/jobs.py:38–46` 的 `_reason` 仅将 `completed` 且 `ok is True` 视作任务成功。
- `tests/test_outcomes.py::test_failed_distillation_is_distinct_from_successful_empty_output` 覆盖错误、脱敏、重开后的原文数量及合法空结果；`test_missing_llm_is_not_reported_as_completed_distillation` 覆盖缺模型、禁用和空会话。
- `tests/test_management_jobs.py::test_retry_reuses_id_and_keeps_previous_failure_receipt` 覆盖同一任务 ID 的重试与历史失败回执。

**判定**：已有明确覆盖；历史 provider 仍失败的记录不能被合成模型成功替代。回归应同时断言原文保存、状态诚实和重试结果，不能只断言 `ok` 或结论数量。

### 2. 关闭回执在任务中心与采集入口的成功判定不一致

**合成输入**：导入返回成功；关闭分别返回 `no_episodes`、`cancelled`、未知状态、缺失状态，以及 `completed` 搭配 `ok=false`。

**当前直接复现**：

| 关闭回执 | `watch.ingest` 开工阶段结果 | 证据含义 |
|---|---|---|
| `error` / `unavailable` / `disabled`，`ok=true` | `close_failed` | 已知失败状态处理正确 |
| `completed`，`ok=true` | `closed_ok` | 明确完成 |
| `no_episodes`，`ok=true` | `closed_ok` | 空会话被当作已提炼 |
| `cancelled`，`ok=false` | `closed_ok` | 明确取消被当作已提炼 |
| `unknown`，`ok=true` | `closed_ok` | 未知状态被当作已提炼 |
| 无 `outcomes_status`，`ok=true` | `closed_ok` | 旧协议兼容行为，不能据此断言提炼成功 |
| `completed`，`ok=false` | `closed_ok` | 未检查顶层失败标志 |

**代码与覆盖**：`engram/connectors/watch.py:396–405` 采用三个失败值的排除判断；任务中心 `engram/management/jobs.py:38–46` 则采用明确成功条件。`tests/test_agent_sessions.py:617–636` 的 `test_close_status_receipts_keep_success_and_failure_distinct` 明确允许缺失状态成功，但未覆盖 `no_episodes`、`cancelled`、未知值及 `ok=false`；任务中心的 `test_ok_true_does_not_misreport_distillation_success` 已覆盖其中部分失败状态。

**最有价值的补齐**：同一组回执经过任务中心、watcher 和 hook 后，应保持相同的完成含义。对于缺失 `outcomes_status` 的旧服务，必须明确选择“兼容但状态未知”或“要求升级”，不能静默把未知等同于完成。该探针证明客户端判定行为，不证明当前服务实际产生过这些不一致回执。

**本轮结果**：`watch.py:400–407` 现在拒绝所有显式非 `completed` 状态，并要求带显式状态的完成回执同时具有 `ok is True`；`ok=false` 永不成功。`tests/test_reliability_capture.py::test_non_success_close_receipt_does_not_claim_transcript` 的七组回执同时验证 watcher 与 hook 不写成功 claim。[采集基线](validation/reliability-20261030/capture-baseline.txt)记录这七组失败，[采集回归](validation/reliability-20261030/capture-after.txt)记录相关回归通过。旧服务完全缺失状态的兼容分支仍保留，其完成语义未由新协议确认，属于明确边界。

### 3. 采集过程中原文件增长，完成记录不能覆盖未采集内容

**合成步骤**：创建合成 JSONL；在解析完成、关闭返回前追加一条新消息；让当前采集成功；随后运行一次 pending 检查。

**期望结果**：完成记录最多确认这次真正解析并发送的版本。新追加内容仍在下一轮队列里；不以关闭返回后文件的最新长度确认整份文件。

**源码依据与覆盖**：

- `engram/connectors/watch.py:196–219` 的 `pending_sessions` 以文件大小匹配 `seen`、hook claims 和 `close_deferred`。
- 开工阶段 `run_once` 在请求完成后执行 `seen[p] = os.stat(p).st_size`（约第 598–600 行），因此“发送版本”和“确认版本”存在时间间隙。
- `engram/connectors/session_hook.py:222–275` 在请求前取 `st`，最终 claim 使用此前的 `st.st_size`；它与 watcher 的确认时间点不同。hook 还有锁释放与 claim 写入的相邻边界，需以实际复现决定是否构成重复执行。
- `tests/test_session_hook.py::test_pending_sessions_skips_what_the_hook_already_fed` 和 `tests/test_agent_sessions.py::test_failed_close_is_deferred_after_three_failures_without_being_marked_seen` 只覆盖两次采集之间增长，不覆盖一次采集请求进行中增长。

**本轮结果**：`tests/test_reliability_capture.py::test_growth_during_close_stays_pending_after_restart` 在[基线](validation/reliability-20261030/capture-baseline.txt)中对成功关闭和第三次失败均复现失败。修复用请求前大小写入 `seen/deferred`（`watch.py:532–537`、`604–616`）；第二次运行实际导入尾部新增消息，原文件仍在 backlog 的断言通过。`test_lost_import_receipt_retries_after_restart_without_duplicate_episode` 还覆盖服务端已提交、回执丢失后的重新导入：重开服务后原文保持一条，仅返回 skipped。没有改动 hook 的锁／claim 相邻边界，也不声称验证了所有文件重写方式。

### 4. 保存中断后，原文、事实与 pending 标记应来自同一个已提交版本

**合成步骤**：先保存一个含 pending episode 的版本；在内存中提炼、生成事实；第二次保存已经替换 `episodes.jsonl`、尚未完成其余集合和 manifest 时注入异常；重新打开临时库。

**期望结果**：恢复到完整旧版本，或完整新版本；不能读到“episode 已 consolidated、事实却仍为旧版本”，因为下一轮就可能不再提炼。损坏已提交数据应明确报错，不得清空已有内存。

**源码依据与覆盖**：

- 开工阶段 `engram/store/persist.py:147–210` 的 `save_memory` 逐集合替换 JSONL，然后替换 manifest；`_write_jsonl` 的单文件原子替换不等于多个集合共同提交。
- `engram/ingest/pipeline.py:52–54` 的 pending 队列直接由 `Episode.consolidated` 派生，所以 pending 并非只是一份可独立重建的 UI 状态。
- `tests/test_persist_crash.py` 覆盖 manifest 之外的尾部破损、已提交记录缺失、失败加载不清空现存内存；`tests/test_persist_roundtrip.py::test_jsonl_roundtrip_preserves_bitemporal_and_state` 覆盖正常往返。
- 这些已有用例没有在多个文件替换之间中断，也没有断言 pending 标记与新事实共同恢复。

**本轮结果**：[持久化失败对照](validation/reliability-20261030/persistence-before.log)记录重复保存中断和缩短快照导致的三个失败；新增 `tests/test_reliability_persistence.py` 覆盖回滚、首次保存中断、恢复再次中断、文件权限与真正子进程 `os._exit`。[持久化回归](validation/reliability-20261030/persistence-after.log)记录相关用例通过。修复在同一文件锁内备份已提交集合，并以备份标记作为整组提交边界（`persist.py:105–169`），读路径先恢复再加载（第 315–318 行）。本轮独立的 pending 探针确认回滚后的 episode 未被误标完成。额外完整备份带来复制与磁盘成本；未验证实机断电或性能收益。

### 5. 并行提炼异常不能被当作“提炼成功但没有事实”

**合成步骤**：为两个 episode 注入提炼器，一条成功、一条抛异常；分别运行单阶段提炼器和两阶段 `raw_items` / `facts_from` 提炼器；随后替换为正常提炼器重试。

**期望结果**：失败 episode 保持可重试；不能因本轮发生异常而退出 pending。已成功事实可保留，但重试不能重复；任务或调用方必须收到失败证据。合法空结果应仍能正常完成。

**源码依据与覆盖**：开工阶段 `engram/consolidate/engine.py:86–109` 将多 episode 提炼异常替换为 `[]`，第 159 行统一置 `consolidated=True`；单 episode 分支第 110–112 行直接抛出异常。`Memory.consolidate` 从 `ingestor.pending()` 获取重试对象（`engram/memory.py:365–371`）。

**本轮结果**：[开工快照重放](validation/reliability-20261030/consolidation-frozen-before.log)记录四个失败：三个并行提炼阶段吞异常，以及关闭任务误报成功。`engine.py:85–105` 现在在写事实和完成标记前传播提炼异常；`tests/test_reliability_consolidation.py` 的九例覆盖单阶段／raw／convert、单条／两条、合法空结果、重开后的重试与无重复事实。[相关回归](validation/reliability-20261030/consolidation-after.log)通过。本轮没有改动 reconcile 或事实存储阶段的事务模型，也不将提炼阶段的保护扩大称为整条 System-2 的事务保证。

### 6. 任务恢复与采集延期是不同状态，恢复条件必须明确

**合成步骤**：分别持久化未执行任务、无活跃锁的 running 任务、仍持锁的 running 任务；重建服务并恢复。另让 watcher 连续三次关闭失败，随后仅恢复模型、保持文件内容不变。

**期望结果**：

- 任务 `pending` 重启后可执行；无活跃锁的 `running` 标为 `failed/interrupted` 后可重试；有活跃锁的任务不被误判中断。
- watcher 的失败预算耗尽是 `close_deferred`，不能被计为 `seen` 或成功。用户/运维需要知道恢复模型本身是否会自动重新排队。

**已有覆盖**：

- `engram/management/jobs.py:209–221` 的 `recover` 与第 320–342 行的消费循环分别处理 running 与 pending。
- `tests/test_management_jobs.py::test_restart_resumes_persisted_pending_work`、`test_restart_marks_unlocked_running_attempt_interrupted_and_can_retry`、`test_async_running_and_live_lock_survive_another_recovery` 覆盖三种任务状态。
- `tests/test_agent_sessions.py:400–431` 已证明三次关闭失败后文件进入 `close_deferred`，不进入 `seen`；文件增长后重新排队。
- 开工阶段 `pending_sessions` 对相同大小的 `close_deferred` 直接跳过。因此“provider 恢复、文件不变”不会仅凭下一次定时触发自动恢复；源码注释提供的条件是文件变化或清理该延期记录。

**判定**：这是已有重试策略的明确边界，不能仅因人工重试可以成功而声称自动恢复已验证。最有价值的后续用例是通过现有受支持的重试入口恢复延期项，同时保持历史失败、幂等及其它延期项不变；是否新增自动延期重试属于单独的产品取舍。

## 可离线重放的关闭回执探针

从仓库根目录运行。所有服务调用均替换为内存函数，`http://unused` 不会被请求；消息及标识均为合成值。它输出当前行为，不把当前错误写成期望断言。

```bash
python3 - <<'PY'
from types import SimpleNamespace
from unittest.mock import patch
from engram.connectors import watch

session = SimpleNamespace(
    session_id="synthetic:case-status", event_time=None,
    messages=[SimpleNamespace(speaker="user", content="The synthetic release remains blocked.")],
)
cases = [
    ("error", True), ("unavailable", True), ("disabled", True),
    ("completed", True), ("no_episodes", True), ("cancelled", False),
    ("unknown", True), (None, True), ("completed", False),
]
for status, ok in cases:
    def post(base, key, route, body, timeout):
        if route == "/v1/import":
            return {"ok": True, "sessions": 1, "episodes": 1}
        reply = {"ok": ok, "outcomes": 0}
        if status is not None:
            reply["outcomes_status"] = status
        return reply

    with patch.object(watch, "load_sessions", return_value=[session]), \
         patch.object(watch, "_post", side_effect=post):
        result = watch.ingest("http://unused", "synthetic", ["synthetic.jsonl"])
    print(status, ok, result["closed_ok"], result["close_failed"])
PY
```

## 唯一一轮独立核验结论

核验只针对本轮三条实际改动及固定案例，没有扩展为发布级审计。独立执行命令：

```bash
python3 -m pytest tests/test_reliability_consolidation.py \
  tests/test_reliability_persistence.py tests/test_reliability_capture.py \
  -q -o addopts=''
# 25 passed in 6.59s
```

| 核验问题 | 结论与限制 |
|---|---|
| 固定故障是否有前后对照？ | 已读取三条路径的失败日志与相关修复后日志；独立运行修复版 25 例通过。未将旧产品验收当成本轮结果。 |
| 保存中断和再次恢复中断是否一致？ | 固定用例覆盖普通异常、SystemExit、子进程 os._exit、恢复中途再中断。独立 pending 探针恢复旧版本后再次提炼成功。未测试硬件断电。 |
| 采集是否确认了尚未上传的尾部？ | 修复后成功和失败延期都只确认采前大小；下一轮补入尾部。丢回执重启重试保持原文幂等。 |
| 提炼失败和合法空结果是否区分？ | 九例覆盖三个异常阶段及合法空结果；失败保持 pending，任务不误报成功。未扩大到 reconcile／存储阶段全事务保证。 |
| 跨入口状态是否一致？ | 显式失败、取消、空会话、未知状态均不会写成功 claim；缺状态的旧协议兼容仍保留，不能解释为已验证真实提炼。 |
| 是否证明线上或 provider 恢复？ | 没有。验证仅使用合成数据、离线替身及本机回环 HTTP。 |

本次案例整理与独立核验只新增本文，未改实现、未运行全套测试、未访问生产或私人库，也未调用 Engram MCP 写入。没有阻断本轮三条修复路径的明确发现。未更改的边界包括：旧协议缺失状态、未变化文件的 `close_deferred` 自动恢复策略、文件同大小重写、备份成本与实机断电；这些不能在本轮交付中被写成已解决。
