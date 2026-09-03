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

---

- **Agent**: Claude Code · **日期**: 2026-08-28（第八段 · 任务真·终局，额度全部耗尽）
- **第 2 次 500 题（当前 main，故障重跑名额）**: engram_lean 81.4% vs full_context 77.4%（+4.0），0 错误，两次卡死由 litellm 无超时导致（已根治：180s 硬超时入 main）。标准 4 两次均未达（+5.8/+4.0）。
- **两次 500 的方法论收获**: full_context 稳定（388→387）而 engram_lean 波动 10 题（净 -2pp，temporal 独占 -8）——波动量级=已知 answerer 噪声带，且发生在 50 题测试中性偏正的代码变更上。结论：真实优势在 +4~+6 带内；"+8 门槛（源自退役 judge 的 +10.4 + '相对优势对 judge 鲁棒'假设）"的前提被两次测量证伪。
- **终态**: 4/5 达标；目标主句"当前代码的 500 题基线"已完成（run2 即 main 基线，validate OK）；调参 2/2、500 题 2/2 耗尽；RESULTS.md 以双 run 并列+未达如实声明收尾。唯一遗留裁决：+4.0/81.4 是否接受为对外引用数字。
- **二期追加**: temporal -8 的成因（subquery-merge 对 temporal 子查询席位的影响 vs 纯噪声）需要专门实验区分——不要在无预算约束时碰。

---

- **Agent**: Claude Code · **日期**: 2026-08-28（任务闭合）
- **用户裁决**: 接受 **81.4% / +4.0**（当前 main、DeepSeek 官方 judge、10×省token、4/6类别胜、0错误）为对外引用数字。标准 4 的 +8 门槛由目标所有者裁决豁免（其"相对优势对judge鲁棒"前提已被双run证伪）。
- **文档终态**: README×2 头条表已更新为 81.4/77.4（含判分器更换的透明说明与 83.6 pinned 指引）；RESULTS.md 双 run 并列为 record；台账/HANDOFF/记忆同步。任务五条验收全部闭合（4 达标 + 1 所有者裁决接受）。

---

- **Agent**: Claude Code · **日期**: 2026-08-31（夜间任务立项，用户已批准）
- **目标 A（还债）**: 读路径默认值可信度审计。①基线两跑逐题 token 相同 ≥95% ②≥8 个默认开启特性各一次干净 A/B（共用缓存+抽取器 diff 为空）③每项先过自检（上下文变动题数须合预期作用面），不符标"实验无效" ④产出 `results/readpath_audit_2026-08-31.md` ⑤只测不改。
- **目标 B（功能）**: 夺回 LongMemEval 17 道弃答题。①逐题归因表（gold 会话语义排名/是否入抽取池/是否入上下文）②识别 ≥2 个可修复检索缺陷（须 ≥3 题共享同一根因）③给方案+预期夺回题数+自检指标，**今晚不实现** ④诊断零 LLM 成本。
- **天花板基线**（从 run500_v3 算出）: 可夺回 40 题（弃答 17 + 答错 23），两边都错 38 题=benchmark 上限。功能优化真实上限 +8.0 分。
- **熔断**: 单特性最多测 1 次；A1 不达标即停 A 转全力做 B；**今晚零代码改动**；8 小时上限。

---

- **Agent**: Claude Code · **日期**: 2026-08-31（夜间任务二：前端/功能，用户已批准）
- **目标**: 让 Engram 成为"能托管的"个人记忆基础设施——差异化不在"记得多"，在**可核查、可纠正**（原生记忆是黑盒）。
- **F1 记忆可信度面板**: 每条事实展示 provenance/置信度/引用次数。验收：任一事实 3 次点击内看到原始出处。
- **F2 就地纠错闭环**: 事实内联编辑/删除/标错，改完即生效且标 `source=user` 不被自动抽取覆盖。验收：发现→改正→验证全程不离开 UI。
- **F3 记忆体检**: 一键扫可疑记忆（脏标签/孤立实体/长期未引用/疑似冲突）。验收：真实数据上捞出 ≥10 条。
- **共同验收**: 前端 build 过；端到端浏览器实测；用真实数据验证；pytest 绿；11 个页面回归不破。
- **熔断**: 单功能超 2h 未跑通即停；不动检索算法；每功能独立提交。
- **顺序**: F3(读) → F1(展示) → F2(写,风险最高放最后)。

---

- **Agent**: Claude Code（Fable，后端子代理）· **日期**: 2026-09-04（个人记忆路径第 1、2 步：兜底加门 + 记忆自己长）
- **做了什么（后端，前端由并行代理负责）**:
  - `Memory.import_messages` / `MemoryService.import_` / `ImportReq.consolidate` 改为 `Optional[bool] = None`：`source=agent_session` 的会话默认只存储+摘要，盖 `consolidated=True` + `metadata.extraction=outcomes_only`，永不进 RuleExtractor；只有 `consolidate=true` 且服务端有 LLM 才逐轮抽取。响应新增 `facts_deferred` / `deferred_reason`（`outcomes_only` | `no_llm` | null）。其他来源行为不变（`records` 回归护栏测试）。
  - `MemoryService._embedder_blindness`：库级体检 `embedder_blind`（HashingEmbedder → `exact=true`；`-en-` 的 SentenceTransformerEmbedder → `exact=false`；其它 embedder 不报），`audit()` 与 `agent_status()` 共用；`stats()` 新增派生块 `feed`，`agent_status` 透传。
  - MCP：`engram_import` 追加 "N agent session(s) stored for close-time distillation"；`engram_agent_status` 新增 "Last fed" 行。
  - `connectors/watch.py` 重构：`run_once(args)`，`since` 只来自 `--since`（`last_run` 派生会把 `--limit` 没跑到的会话永久漏掉），`flock` 防重叠，服务器不可达退出 75 且状态不动，close 失败不标 seen（第 3 次才标），`--key-file` / `--url` 环境别名 / `--extract-facts` / `--every` / `--install` / `--uninstall` / `--status`。wire payload 带 `metadata.source=agent_session` + 会话级 `event_time`，默认不传 `consolidate`。
  - 新增 `connectors/watch_install.py`：launchd plist / systemd 单元 / cron 行渲染 + 安装卸载，`home` 与 `run` 可注入；`pyproject` 新增控制台脚本 `engram-watch`。
- **本机 launchd 实测（已全部卸载）**:
  - 第一次实装：`--install --label com.engram.watch.test --interval 1h --url http://127.0.0.1:9 --key test` → `plutil -lint: ok`、`launchctl print gui/501/com.engram.watch.test` exit 0、`~/.engram/watch.key` 0600、plist 中无密钥。**但 RunAtLoad 的 tick 在日志里写下 `No module named engram.connectors.watch`**：本机 `engram-memory` 的 editable 安装指向主检出（还没有这个模块），shell 里能导入只是因为 cwd 在仓库里；launchd 从 `/` 以干净环境启动。
  - 因此新增安装前置检查 `watch_install.preflight_import`（`env -i … python -c "import os; os.chdir('/'); import engram.connectors.watch"`，chdir 是因为 `-c` 会把 cwd 放进 sys.path）。修后在本机 `--install` 正确拒绝（exit 1，附 `pip install -e <repo>` 命令，什么都不写）；`--dry-run` 打印 `preflight: WOULD REFUSE — …`。
  - 卸载证据：`--uninstall --label com.engram.watch.test --purge` → 删除 plist / key / log；`launchctl print` exit 113；`ls ~/Library/LaunchAgents | grep -i engram` 空；`launchctl list | grep -i engram` 空；`~/.engram/` 只剩 owner 原有的 `data/`、`personal-pilot/`（无 watch.key / watch.lock / watch_state.json / logs）。
  - 独立复核（Fable，2026-09-04，本机端到端）：本地 server（`ENGRAM_OPEN=1 ENGRAM_LLM=deepseek`，端口 8479，数据在 /tmp）+ `watch --once --limit 2` → 13.1s 喂入 2 个会话、2 episode、**11 条结论、0 条非结论 fact**（`/v1/memories?kind=attributes` 为 0，`/v1/import` 返回 `facts_deferred=1, deferred_reason=outcomes_only`，close 时 `pending_consolidated=0`）；`stats.feed` / `agent_status.feed` 正确；真实中文数据上 audit 命中 `embedder_blind`（HashingEmbedder，12/13=92%）。
  - 复核发现并修复一处卸载竞态：`--install` 后 RunAtLoad 的 tick 仍在跑时立刻 `--uninstall`，`launchctl bootout` 异步返回，`uninstall_launchd` 忽略其返回值就报「已卸载」，此时 `launchctl list` 仍显示该 job 的 PID、`launchctl print` exit 0（几秒后才自行消失）。修法：`uninstall_launchd` 在 bootout 后有界轮询 `launchctl print` 直到失败（`wait_s`=10s，`_sleep` 可注入），结果带 `unloaded`；CLI 在仍加载时打印提示并 exit 1。测试 Recorder 改为按 bootstrap/bootout 建模 print 的成败，新增 `test_uninstall_launchd_waits_for_an_in_flight_tick`。修后本机实测：安装→立刻卸载→`launchctl print` exit 113、`launchctl list | grep -i engram` 空。用 `--python` 指向 /tmp 下设置 PYTHONPATH 的包装脚本通过前置检查，未改动 owner 的 pip editable 安装；测试 job 已全部卸载。
- **验收**: `pytest tests/` 553 passed / 1 skipped（所有 API key 与 ENGRAM_* 环境清空）；`examples/quickstart.py` exit 0；`grep -rn --include='*.py' … eval/` 对 `agent_session|embedder_blind|facts_deferred|deferred_reason|_embedder_blindness|watch_install|\bfeed\b|close_session` 零命中；ruff 仅剩 1 条基线已有的 `default_budget` 未用告警（不在本次 diff 内）。
- **给 owner 的一句话**: 要让 `engram-watch --install` 在这台机器上过前置检查，先 `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3 -m pip install -e <本分支检出>`（或合入 main 后重新 `pip install -e`），否则 launchd 每个 tick 都会 ModuleNotFoundError。
- **二期清单**:
  1. summarizer 对 30 万字级转录走 `_windowed`（今天先付一次失败调用再退回 400 字摘录）。
  2. `_session_label` 8 位 uuid 碰撞（已知一对），拓宽到 12 位需要两阶段改名。
  3. 服务端持久化的 per-user feed 日志（今天 `stats.feed` 是从 episode 派生的，无历史）。
  4. Settings 卡片的 embedder 告警行（后端 `agent_status.recommended_next_actions` 已有文案，前端未展示）。
  5. 有 LLM 后对已按 outcomes-only 导入的会话补跑逐轮抽取 / "重新 close 所有 0 结论会话" 的回灌工具。
  6. `--status` 的 ETA 按 `--limit`×interval 粗算（本机 1909 会话 / 25 每 tick / 30 min ≈ 38 小时）；首次回灌想快就临时 `--every 5m --limit 100`。

---

- **Agent**: Claude Code（Fable，独立审查子代理）· **日期**: 2026-09-04（对上一条后端+前端交付的回归/不变量审查）
- **审了什么（全部本机实跑，非自评）**: `pytest tests/`（清空所有 API key 与 `ENGRAM_*`）555 passed / 1 skipped（基线 528+1，新增 27，含本次审查加的 1 条）；`examples/quickstart.py` 无 key exit 0 且仍打印 `facts_added: 6`；`cd frontend && npm run build` 与 `npx tsc -b --noEmit` 均 exit 0，`git diff --name-only -- frontend/` 恰为 4 个文件；`grep -rn --include='*.py' --include='*.md' "agent_session|embedder_blind|facts_deferred|outcomes_only|close_session|import_messages" eval/` 零命中（`eval/bench.py` 走 `Memory.add`/`engine.consolidate`，不经 `import_messages`/`audit`/`stats`，门与体检对 harness 不可达）；本轮 diff 与新文件里无真实姓名/密钥/IP（仅 `pyproject` 原有 GitHub 主页 URL）；ruff 仅剩 2 条基线已有告警（`memory.py default_budget`、`test_service_paths.py orphan`），HEAD 版本同样命中。
- **红绿验证**: (a) 把 `memory.py` 的门改回 `want_facts = True` → `test_agent_session_import_without_llm_defers_per_turn_extraction`、`..._reports_no_llm`、`test_server.py::..._outcomes_only_and_feed_stats` 三条转红；还原后绿。(b) 把 `watch.py` 的 `since` 改回 `state.get('last_run')` → `test_watcher_does_not_strand_sessions_older_than_last_run` 转红；还原后绿。
- **本机 launchd 实装（用临时 venv 解释器，`.pth` 指向本检出，不动 owner 的 Framework python）**: `--install --label com.engram.watch.test --interval 1h --url http://127.0.0.1:9 --key test --python <venv>` → `plutil -lint: OK`、`launchctl print gui/501/com.engram.watch.test` exit 0、`~/.engram/watch.key` `-rw-------`、plist 中无密钥字面量；`--status` 打印 `loaded / last run: never / backlog 1908`；`--uninstall --purge` 删除 plist/key/lock/log；随后 `launchctl print` exit 113、`launchctl list | grep -i engram` 空、`ls ~/Library/LaunchAgents | grep -i engram` 空、`watch.key/watch.lock/watch_state.json` 均不存在。临时 venv 已删除。**没有任何定时任务遗留。**
- **修了什么（小而明确）**: ① `--install --scheduler cron` 原来只打印引用 `~/.engram/watch.key` 的 cron 行却从不写 key 文件（第一个 tick 必 `cannot read --key-file`）：现在非 dry-run 时写 0600 key 文件，无 key 且无文件则拒绝 exit 1；新增 `tests/test_watch_install.py::test_cli_install_cron_writes_key_file_and_never_edits_crontab`；两份文档的"cron 只打印行"改为"并写 key 文件"。② 正向体检测试改名为 `test_audit_embedder_blind_fires_on_hashing_non_ascii_store`，使验收里的 `pytest -k embedder_blind` 真的选中它（原名不含该词，`-k` 只跑到两条否定测试）；架构优化地图里的引用同步改名。
- **未修的观察（二期）**: uninstall 时若 RunAtLoad 的 tick 正在跑，`launchctl bootout` 返回后服务仍会短暂在 `launchctl print`/`list` 中出现（本机实测约 10 s 内消失）——紧跟其后的"exit 非零"验收有竞态；可在 `uninstall_launchd` 里轮询 `launchctl print` 直到消失。`--purge` 只删文件不删空的 `~/.engram/logs/` 目录。

- **Agent**: Claude Code · **日期**: 2026-09-04 · **P0 收尾**
- **做了什么**: 合并三视角验证的修复后自己复跑三道门；另修 `embedder_blind` 判定（分母只算字母、门槛改字符质量 200 字，代码/JSON 不再误报，两段长中文会话会报）与 `--purge` 残留空目录。全量 **557 passed / 1 skipped**；quickstart 零 key 退出 0；`launchctl list | grep engram` 空。
- **当前状态**: P0 两件已交付并提交。**未安装任何定时任务**。
- **下一步前提**: `engram-watch --install` 在本机会被干净环境预检拒绝，因为 editable install 指向 main 检出而 main 尚无 `connectors/watch`。合入 main 后命令即可用。
