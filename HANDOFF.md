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

---

- **Agent**: Claude Code · **日期**: 2026-08-27（第二段）
- **做了什么**: ① PR #23 已合并进 main（48a2369）。② 线上 42.193.220.197:8456 已升级到含全部修复的最新代码：rsync 同步 engram/ + frontend/dist + pyproject + tests 到 /home/ubuntu/engram-memory（该目录非 git 仓库，是 rsync 快照），`systemctl restart engram` 完成切换。
- **验收证据**: /health 带 version=0.1.0、/ready 200、/metrics 200、demo key 1 数据完好（117 facts/72 episodes）、畸形 import 返回 400、线上 export→import 回路实战通过（2 facts 恢复+recall 命中+幂等重灌全 skipped）、UI Settings 无横向滚动+新资产加载。
- **重要事实修正**: 线上数据一直是持久的（ENGRAM_DATA_DIR=/home/ubuntu/engram-memory/data，291 个命名空间目录；health 的 storage:memory 指内存型索引后端，不代表不落盘）。升级前额外做了 key=1 备份：服务器上 ~/engram-key1-backup-0827.json。
- **服务器运维发现**: 该机是 4 核 7.4G 多业务共享机（okx 量化占 3.3G 内存、ekos docker 全家桶、8457 端口另有 engram-personal 私人实例——勿动）。内存长期耗尽、swap 打满、kswapd 狂转，2026-08-27 下午曾负载爆表（load 216）导致全机失联约 20 分钟。deploy/docker-compose.demo.yml 已入库但在这台机器上**不要用 docker build**（内存不够），rsync+systemctl 是当前合适的部署方式。
- **遗留**: ① 服务器内存压力是系统性风险，建议用户评估迁移/扩容或收敛业务；② ENGRAM_LLM 仍未配置（规则抽取质量有限），配置需要用户提供 LLM key 并写入 /home/ubuntu/engram-memory/.env 后重启。

---

- **Agent**: Claude Code · **日期**: 2026-08-27（第三段）
- **做了什么**: 线上实例配置 LLM：`.env` 追加 DEEPSEEK_API_KEY / ENGRAM_LLM=deepseek / ENGRAM_ANSWERER=deepseek（原 .env 备份为 .env.bak-0827），重启生效。
- **验收证据**: health `llm_configured:true` + `answerer_configured:true`；抽取质量对照——"My dog is named Rex. I work as a data engineer at Acme Corp." 规则引擎旧输出 `occupation: named Rex`（错），DeepSeek 输出 `owns Rex`/`works at Acme Corp`/`job title data engineer`（对）；`/v1/chat/completions` 从 503 变为带记忆的正常回答。
- **备注**: DeepSeek 现为 v4 系列，`deepseek-chat` 别名由服务端映射到 `deepseek-v4-flash`（已实测）。key 在服务器 .env 与用户掌握中，勿写入仓库。
