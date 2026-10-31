# 采集恢复可靠性验证（2026-10-30）

本轮新增改动仅为 `engram/connectors/watch.py` 与 `tests/test_reliability_capture.py`。`session_hook.py` 与开工快照哈希相同，原有未提交改动保留。1 个实现候选，无提交、部署、真实目录采集或私人记忆写入。

## 已复现与修复

| 缺陷 | 开工源码表现 | 修复后 |
|---|---|---|
| 关闭期间文件增长时错误推进大小游标 | 合成 JSONL 采集前 2931 字节；HTTP close 期间追加后 3053 字节。原文尾部未到服务器，但成功分支把3053记为seen；第三次关闭失败把3053记为close_deferred。重载状态后尾部不再排队。 | 采集前大小进入attempted_sizes；seen/deferred只记录该次尝试的大小。检测到增长时保留backlog，下一轮采集尾部。成功与重试耗尽两条路径均有回归。 |
| 把非成功关闭回执当成功 | no_episodes、cancelled、unknown、null、completed但ok=false/缺失，以及仅ok=false，均被加入closed_ok。 | 显式outcomes_status仅允许completed且ok is True。其余状态作为close_failed；SessionEnd hook不写claim。 |

兼容边界：旧服务完全不含 `outcomes_status` 字段且没有明确 `ok=false` 的回执，继续按原有协议接受；现有旧协议回归保留。该兼容回执本身不提供可核验的提炼状态。显式 `outcomes_status: null` 不属于旧协议缺字段。

## 证据与验证范围

- 固定新增用例10项在开工快照上：**9 failed, 1 passed**，见 `capture-baseline.txt`。失败为两条增长分支及七种关闭回执。
- 修复版新增10项 + 已有 `test_agent_sessions.py` / `test_session_hook.py`：**76 passed**，见 `capture-after.txt`。
- 已通过的丢回执回归：真实离线MemoryService完成import并持久化后，loopback服务器主动断开连接；watch未记seen。重新创建MemoryService、重试原文后返回skipped=1，episode总数仍为1，后续无重复POST。
- HTTP使用标准库ThreadingHTTPServer，仅绑定127.0.0.1；每例使用临时合成JSONL、合成key与隔离data目录；模型名称显式为空；HTTP代理禁用。未接触外部服务或模型。
- 增长回归中的两次不同完整会话快照会保存为两个episode，这是现有按完整内容指纹的模型；本轮未改变该数据形态。丢回执重复发送同一快照不会重复入库。
- `capture-change.patch` 为相对开工快照的唯一源码差异；`capture-manifest.json` 固定源码和回归文件SHA-256。`git diff --check` 已通过。
- 早期 `capture-before.txt` 为最初9项用例的失败记录；最终冻结证据以 `capture-baseline.txt` 的10项为准。

## 复现命令

修复前（开工快照目录由 `/tmp/engram-reliability-current-path` 提供）：

```sh
cd /var/folders/zb/ghclng_x1gvcg7sv109n5_dr0000gn/T/engram-reliability-20261030-9fw4p410/baseline
PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m pytest '/Users/ywwl/Documents/Claude/Projects/Super memory/tests/test_reliability_capture.py' --noconftest --import-mode=importlib -o addopts='' -q
```

必须使用 `--noconftest`：工作树根 `conftest.py` 会把它自己的根目录插入sys.path首位。外部绝对测试路径若加载该文件，会错误地验证修复版；这类运行不作为baseline证据。

修复后：

```sh
cd '/Users/ywwl/Documents/Claude/Projects/Super memory'
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m pytest tests/test_reliability_capture.py tests/test_agent_sessions.py tests/test_session_hook.py -o addopts='' -q
git diff --check -- engram/connectors/watch.py engram/connectors/session_hook.py tests/test_reliability_capture.py
```

## 交付边界

本轮只证明合成失败路径及恢复回归。原先真实provider凭证失败没有调用或恢复验证；不宣称自动提炼已恢复。文件原地同大小改写、进程强杀时的hook_claim轮转等未作为本轮缺陷扩展。架构文档与总体验证由主会话统一维护。
