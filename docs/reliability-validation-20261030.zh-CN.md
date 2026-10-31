# Super memory 本地可靠性修复验证

日期：2026-10-30。读者：项目负责人和后续维护者。

本轮修复了三条已在开工代码上复现的数据可靠性缺陷：批量提炼失败被当作完成、快照保存中断导致新旧数据混合、采集期间增长的原文被错误确认。验证使用合成原文、离线提炼替身、临时存储及本机 HTTP 服务。改动已在本地工作树，尚未提交、发布或部署。

## 修复与证据

| 路径 | 原行为与影响 | 修复后行为 | 同一固定用例的前后结果 |
| --- | --- | --- | --- |
| System-2 批量提炼 | 并行阶段吞掉异常并把原文标为 consolidated，缺失事实可能永不重试；关闭任务可误报成功 | 提炼或转换异常在写事实、完成标记之前传播；原文保持 pending，可重开后重试；合法空结果仍可完成 | 4 失败、5 通过 → 9 通过 |
| JSONL 快照 | 多文件逐一替换，写到一半失败时可能混载，甚至旧快照不可读 | 保存前保留完整旧版本；提交标记覆盖全部文件；读写共锁，未完成事务可恢复，再次中断仍可重试 | 6 失败 → 6 通过 |
| Agent 原文采集 | 关闭期间新增尾部也被最新文件大小确认；部分异常回执被当作成功 | seen/deferred 只记录尝试前大小，文件增长继续排队；显式状态仅 completed 且 ok=true 成功 | 9 失败、1 通过 → 10 通过 |

三条路径合计 **25 个固定故障回归：开工快照 19 失败、6 通过；修复版 25 通过**。开工快照包含本轮之前已有的未提交改动，比较对象不是 Git HEAD。源码和测试 SHA-256 在 [comparison.json](validation/reliability-20261030/comparison.json)，逐例结果在 [修复前](validation/reliability-20261030/combined-before.log) 和 [修复后](validation/reliability-20261030/combined-after.log)。这些是故障回归，不能换算为问答准确率或真实用户效果提升比例。

## 整体验证

- [完整离线 pytest](validation/reliability-20261030/full-pytest.log)：878 passed、1 skipped、1 warning。跳过项为已安装 pypdf 时不适用的缺依赖路径；warning 来自本地 HTTP 模型替身的 LiteLLM/Pydantic 序列化兼容提示。
- [零配置检查](validation/reliability-20261030/zero-setup.log)通过：包括源码及模块 quickstart、离线 harness、已有结果完整性和标准库编译。
- 三个修改模块与新增测试的 Ruff、git diff --check 通过。
- [案例及一次独立核验](reliability-cases-20261030.zh-CN.md)未发现阻塞；独立固定回归 25 通过，另验证 pending 在保存中断后恢复并再次提炼。
- 统一验证进程使用只允许回环网络的 Python socket guard，关闭外部模型配置及模型下载；没有运行付费在线效果评测。

## 运行改动

修改源码：`engram/consolidate/engine.py`、`engram/store/persist.py`、`engram/connectors/watch.py`。新增三个同名领域的 `tests/test_reliability_*.py`。仅含本轮增量的 [补丁](validation/reliability-20261030/change.patch)保留了原工作树改动；[保存检查](validation/reliability-20261030/preservation.json)未发现允许范围外的开工源文件变化。

在项目根目录执行：

```sh
python3 -m pytest tests/test_reliability_consolidation.py tests/test_reliability_persistence.py tests/test_reliability_capture.py -o addopts= -q
python3 -m pytest -o addopts= -q -ra
python3 scripts/check_zero_setup.py
```

精确的修复前源码、修复后源码、固定测试及重放脚本保存在本机恢复目录；入口见项目 HANDOFF.md 和验证摘要中的 recovery_dir。`replay.py before` 应返回失败码 1 并报告 19 失败，`replay.py after` 应通过；脚本在临时副本运行，不覆盖当前项目。

## 适用边界

快照格式继续使用 schema 1，新增暂存目录 `.snapshot-backup` 和提交标记。保存增加一次全量备份复制及磁盘空间；没有测量新的性能上限或实机断电行为。存在未完成提交标记时，必须先用本版恢复再回退旧版读取器。

本轮保障提炼阶段异常的可重试性，未扩展为事实冲突、图写入及多后端的完整事务。采集保留旧服务器缺失状态字段的兼容；同大小文件重写和未变化 close_deferred 文件的自动恢复策略未改。真实模型凭证失效、在线提炼质量和生产稳定性仍不能由本次本地测试证明。以上非阻断项只登记，不继续扩展本轮工作。
