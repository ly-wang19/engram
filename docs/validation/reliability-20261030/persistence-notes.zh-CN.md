# 持久化中断恢复：本地离线验证

日期：2026-10-30。范围只含 `engram/store/persist.py`、新增 `tests/test_reliability_persistence.py` 与本目录证据；未提交、未发布、未访问外部服务、未读取凭据或写入真实记忆。

## 根因与失败证据

原 `save_memory()` 按顺序替换各集合 JSONL，再替换 `manifest.json`。每个文件的原子替换无法保证整个快照原子性。

- 在 `episodes.jsonl` 替换后、`facts.jsonl` 写入前注入 `OSError` 或 `SystemExit`，重新打开会返回新 episodes 与旧 facts/focus，且不报错。
- 先减少 episodes 数量再注入相同失败，旧 manifest 仍要求原条数，原快照已无法打开。
- 首轮三个确定性失败完整输出见 `persistence-before.log`。这些测试在改动生产代码前编写并实际运行；没有用静态推测代替失败证据。

## 修复

保留原 schema、根目录 JSONL 和 manifest 格式，保存前创建 `.snapshot-backup/`（0700）及保留原文件权限的恢复副本。全部备份同步完成后才原子写入 `ready.json`。它存在时表示新快照尚未提交。所有集合与 manifest 写入并同步后，移除并同步 ready 标记作为提交边界。

- 普通保存异常先尝试恢复旧快照，再传播异常。
- 进程直接退出时，下次 `load_memory()` 或 `save_memory()` 在同一文件锁内先恢复。
- 恢复使用复制而非消耗备份；恢复本身再次中断仍可重试。
- 首次保存无旧 manifest 时，失败恢复为不存在已提交快照的空库。
- 读取与写入共用文件锁，阻止持久化加载看到集合替换的中间状态。
- `flock` 的释放改为 `finally`；`ImportError` 降级只捕获导入失败，避免把被保护的业务异常误当成缺少 `fcntl`。

## 已验证

新增 6 个用例覆盖：普通 IO 异常、`SystemExit`、真实子进程 `os._exit(31)`、缩小快照后失败、首次保存中断、恢复再次中断与旧权限保留；失败恢复后重复保存再打开，内容和数量保持一致。

相关离线回归 43 项通过，输出见 `persistence-after.log`。包括已有持久化往返、损坏 committed prefix、schema/embedder 防护、pickle 显式迁移、zero-setup default、选择性遗忘中断恢复。首次候选的较早 40 项结果保留在 `persistence-after-initial.log`。

```sh
python3 -m pytest tests/test_reliability_persistence.py -q
python3 -m pytest tests/test_reliability_persistence.py tests/test_persist_crash.py tests/test_persist_roundtrip.py tests/test_persist_safety.py tests/test_zero_setup_default.py tests/test_migrate_pickle.py tests/test_selective_forget.py -q
git diff --check -- engram/store/persist.py tests/test_reliability_persistence.py
```

完整开工快照与修复版同案例比较、完整 pytest 和 zero-setup 由主代理统一保存，本文件不把上述 43 项扩大为全仓验收。

## 局限

- 验证环境是本机 macOS，合成临时目录；没有实机断电、文件系统损坏、磁盘满或生产验证。
- `fsync` 和进程退出探针证明本轮路径，不等于跨平台存储设备的断电耐久性认证。文件锁仍按已有支持范围面向 Linux/macOS。
- 每次保存增加一份旧快照备份及一次全量复制，暂未测量性能/容量阈值。
- 完成后的旧备份清理为 best effort；若删除失败，备份保留在 0700 目录中，并由下一次保存清理。本修复不提供物理安全擦除保证。
- 不解决 Memory 对象在保存期间被调用者并发修改、不同 Memory 实例过期写覆盖、可选向量后端跨存储事务，或所有损坏输入的统一校验；这些不冒充已修复。
- 旧版本加载器不理解未完成事务的 ready 标记；中断目录需先由本版本恢复后再交给旧版本。
