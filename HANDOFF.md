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

---

- **Agent**: Claude Code · **日期**: 2026-08-27（第五段）
- **做了什么**: 用 LongMemEval_S harness 跑分时发现并回滚了自己前一段引入的严重回归。
  - `185e0ac`（entity_normalization）在同 4 题同 rig 的 A/B 中把 engram_lean 从 **75% 打到 25%**（knowledge-update、multi-session 均 0%→100% 反向）。
  - 根因：`entity_worthy()` 的阈值按中文语料标定，英文实体误杀 ~27%；且实现是主/宾任一不合格就丢弃**整条边**，相关事实完全失去 graph proximity。
  - 已 `60682c5` 回滚、推 main、rsync + restart 同步线上；全量 pytest 绿；回滚原因与 A/B 证据记入 `docs/architecture-optimization-map.zh-CN.md` 的「未采用/回滚原因」与 `results/entity_normalization_regression_ab.md`。
- **harness 现状（给后续 AI 的重要信息）**:
  - 数据集在 `~/.cache/huggingface/hub/datasets--xiaowu0162--longmemeval/snapshots/*/longmemeval_s`（500 题），bge-small 已缓存，可 `HF_HUB_OFFLINE=1` 离线跑。
  - **RESULTS.md 里的复现命令已部分失效**：judge `volcano:deepseek-v3-2-251201` 端点 404 下线。可用替代：`deepseek`（官方 API，现为 v4 系列）或 `univibe:gpt-5.5`。answerer/extractor 的火山端点仍可用。换 judge 会改变绝对分数，不能与已提交的 83.6 直接比，但同一 run 内 engram_lean vs full_context 仍是同裁判、可比。
  - worktree 需要 `.env`（已软链到主检出）；`--workers 4` 在本机会被 OOM kill（exit 137），用 2–3。
- **教训**: 只在方便取到的语料（用户中文个人记忆）上验证算法改动并宣布成功，违反 CLAUDE.md §4。触及 read path/graph/排序的改动，合并前必须过 harness 切片 A/B。

---

- **Agent**: Claude Code · **日期**: 2026-08-27（第六段 · 持续任务立项）
- **任务目标（用户已批准）**: 把 main 的读路径带到"每个默认开关都有证据、无已知类别塌陷"的状态，并在新 judge 下定格完整 500 题新基线。
- **验收标准（达标即停）**:
  1. 读路径消融表落盘（25 特性 × 60 题，含噪声带，helps/noise/hurts 三档）→ `results/readpath_ablation.jsonl`
  2. single-session（user+assistant）50 题同题恢复至 ≥9/12（现 4/12），temporal/preference 增益不倒退超噪声带
  3. answer-in-context 率不低于消融基线均值
  4. 500 题完整 bench（judge=deepseek 官方，偏离已声明）：engram_lean 领先 full_context ≥+8 分、0 系统性错误、日志提交
  5. RESULTS.md/README/架构地图同步
- **本任务特有熔断**: 调参→重测最多 2 轮；500 题最多跑 2 次（1 正式 + 1 故障重跑）。

---

- **Agent**: Claude Code · **日期**: 2026-08-27（第七段 · 读路径优化任务终局）
- **五条验收终态**: ①消融表✅（噪声带±8.3pp，promotion挤占=全场最大负资产） ②塌陷修复✅（第2轮后 single-session 9/12 达标，增益类回吐1题在噪声内；两轮调参额度用尽即停） ③护栏✅（answer-in-context 90.0% vs 门槛74.2%） ④500题基线⚠️（+5.8 未达预注册的+8门槛；跨judge对照证明压缩主因是标尺：engram 83.6→83.4 vs 基线73.2→77.6；判分审计对称1/500无偏；按熔断不重跑） ⑤文档✅（RESULTS.md 新测量段与 pinned headline 并列共存、未达门槛如实声明、台账两行修复记录）。
- **两个同构缺陷的教训**: promotion 挤占语义chunks、子查询挤占主查询席位——同一形态"辅助信号驱逐主信号"，修复哲学统一为"辅助有界+主信号保底"。后续读路径特性设计时优先检查此形态。
- **二期清单**: graph_path_reinforcement 边缘有害（-9.2pp,超带1.1pp,单测量,复测后决定）; planner_llm_decomposition 零证明收益+延迟成本（默认关闭候选,QA层复核后动）; 纯数字实体入图; judge/extractor 换稳定 provider（volcano 端点持续退役中）。
