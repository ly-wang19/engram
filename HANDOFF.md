# HANDOFF

跨 Agent 交接账本（协作协议第 2 条）。追加式，最新在下。

---

- **Agent**: Claude Code · **日期**: 2026-08-27
- **做了什么**: 对线上实例 42.193.220.197:8456 做了全量系统测试（鉴权/记忆生命周期/冲突消解/11 个 UI 页面），据此修了三个问题并落在分支 `claude/system-check-testing-c40fb8`（基于 origin/main af95b6a）：
  1. `0983525` — export→import 可携权回路闭合：新增 `Memory.import_snapshot()`（facts 保留双时间轴+supersedes 链重映射，episodes 标记已消化不重复抽取，幂等）；`/v1/import` 解析失败 500→400；`sniff` 识别 `engram_export_version`。测试 `tests/test_import_snapshot.py` 8 项，全量 pytest 绿。
  2. `40e8479` — 控制台页面级横向滚动修复（Settings 网格 `minmax(0,…)`+`min-w-0`、Topbar key 胶囊 `min-w-0`）；375px 与 1280px 实测无溢出。
- **当前状态**: 已提交未推送、未合 main。`frontend/package-lock.json` 未入库（构建副产品，待 owner 决定）。
- **关键发现（对齐问题）**: 线上 42.193.220.197:8456 运行的是 **≈2026-06-27~07-09 的旧代码**（缺 `/ready`、`/metrics`、`/v1/documents`，health 无 `version` 字段，UI 构建 6-27），比 origin/main 落后约 2 个月，且不是用 `deploy/docker-compose.yml` 起的（其 healthcheck 依赖线上不存在的 `/ready`）。线上 `storage:memory` 无持久卷，重启丢数据。本地主检出 main 落后 origin/main 10 个提交。
- **遗留/下一步建议**: ① 合并本分支到 main；② 拉齐本地 main（`git pull`）；③ 重新部署线上（建议改用 deploy/docker-compose.yml + `ENGRAM_DATA_DIR` 持久卷 + 配置 `ENGRAM_LLM`）；④ 规则抽取器误判（"My dog is named Rex"→occupation）在接 LLM 后应复验。
