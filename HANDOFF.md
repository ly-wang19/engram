# HANDOFF

## 集成进如我的方案改为整体集成（Claude Code，2026-10-31，仅方案，未改本仓库）

- 本人 10-31 的决定：Engram 前后端整体集成进如我，作为个人记忆模块；AI 引擎改用 Codex；与如我分身互不影响。本条取代下方 10-30 那条评估的结论。
- 方案副本：`/Users/ywwl/Documents/AI 进入企业 AI work/dingtalk-claude-gateway/docs/design/engram-personal-memory-module-plan-20261031.md`。正本在 Claude Doc「如我 AI 代码地图」→「Engram 记忆模块方案」。
- 本仓库要先做的事（第 0 步，尚未开工）：
  - 中文分词：tokenize 加中文二元组，同时改 evidence.py:157、aggregate.py:159/385、conflict.py:39，hashing 嵌入器加版本号；
  - 同一会话只保留最新版（memory.py:502）；
  - 写盘改为增量（persist.py:134-299）；
  - 去掉召回时的全量拼接（service.py:690）；
  - 摘要截窗到 24000 字（summarizer.py:60）；
  - Windows 三处硬阻塞（persist.py:89/99、jobs.py:192）；
  - watch 在 find_sessions 里按 cwd 排除 `/conversations/(o2o|group|mail)-<20hex>`，并支持 CODEX_HOME；
  - 新增 `llm/codex_llm.py` 并在 providers 注册；
  - 嵌入模式：`/engram/ui/` 前缀、免登录、只允许同源 frame。
- 关口 1：中文能搜到；单人库内存低于 300MB；Windows CI 全绿；全量测试通过。

---

## 与如我结合的评估（Claude Code，2026-10-30，仅方案，未改本仓库）

- 方案全文：`/Users/ywwl/Documents/AI 进入企业 AI work/dingtalk-claude-gateway/docs/design/engram-memory-ledger-plan-20261030.md`。
- 结论：如我只会采用一个零依赖的账本内核。
  - 内核包括：Fact、双时间轴、supersedes、本人锁定、结论四分类、证据问答协议、遗忘的预览与确认。
  - 需要补上：SQLite 存储、Windows 兼容锁，以及“原文在外部、证据只是指针”的模式。
  - 不采用 Engram 的服务、MCP、torch 嵌入或 LiteLLM 提炼。
- 本仓库相关发现（只读核实）：
  - watch 没有排除规则，正把如我钉钉会话线程（含第三方消息）写进 `me`。
  - 本机 8766 服务占用约 8.2GB 内存，数据 4.1GB。
  - watch 最近一次产出结论是 09-13，近两轮每轮 25 次 close 失败。
- 下一步：等本人拍板，暂不改代码。

---

## 已完成：本地真实可靠性改进（2026-10-30）

目标与边界：把剩余套餐优先用于真实可靠性；只改本地代码，以合成原文、临时存储和本机 HTTP 验证。不外发、不部署、不改生产/私人实例、不调用付费外部服务；原有未提交改动保留。

本轮验收标准（全部通过）：
1. 基于已有故障证据，至少两条当前代码缺陷可确定性复现。实际冻结三条路径、25项回归。
2. 最小修复并验证异常恢复、重复处理和状态诚实。批量提炼保留pending、快照恢复一致、采集尾部继续排队；关闭回执显式成功才确认。
3. 同一用例修前失败修后通过；相关/完整离线与zero-setup通过。开工快照19失败/6通过，修后25通过；完整878通过/1跳过/1兼容warning，zero-setup、Ruff、diff-check通过。
4. 可运行改动、前后证据、恢复点及两份架构文档齐全。使用3个子代理、1个实现候选、1轮独立核验，未发现阻塞。

交付入口：`docs/reliability-validation-20261030.zh-CN.md`；案例与独立核验：`docs/reliability-cases-20261030.zh-CN.md`；日志、SHA-256、增量补丁：`docs/validation/reliability-20261030/`。

恢复点：`/Users/ywwl/.codex/recovery/super-memory/engram-reliability-20261030-9fw4p410`。包含开工dirty源码、原始diff、修复后文件、25项固定测试和replay.py；不要用git reset覆盖已有改动。开工185个源/测试/选定文档哈希核对，允许范围外改动0。当前代码未提交、推送、发布或安装到服务。

保留边界：备份全量复制代价与实机断电未评测；旧版读取器不认识未完成.snapshot-backup标记，回退前须本版恢复；提炼后的图/冲突写入不是跨后端事务；同大小原文重写、无增长close_deferred自动恢复、旧协议缺状态字段仍在二期。真实provider凭证故障未改变，未将本地替身测试当作线上恢复。

记忆接续：遵守本轮不改私人实例/不调用付费服务的边界，未调用Engram写入或关闭提炼；已确认决定、发现和未解决问题保留在本文件，未另建全局记忆规则。

唯一下一步：无，验收达标即交付。后续如明确继续，先校验恢复点和工作树SHA，再从已登记二期中选一项新验收目标；不得沿用本轮扩大范围。

---

## 已完成：部署六项产品功能（2026-10-30）

用户明确授权“全部部署”。对象为现有 lighthouse 的 engram.service（8456）；范围为本轮六项前后端产品功能及必需依赖。保留当前未提交改动；另一路算法实验仍不在本轮产品发布范围。

验收标准：1. 冻结覆盖六项功能的兼容发布包及哈希，线上基线和依赖验证通过；2. 保留可恢复代码/数据/环境备份；3. 发布后health/ready、六项接口及实际页面回读通过，管理员入口可用；4. 原数据计数/读取保持，独立 engram-personal 服务不变，记录回滚路径。

当前结果：六项已部署，版本 `product-20261030-d64e86214b92`。冻结包兼容回归159 passed / 1 skipped；实际远端环境六项隔离API链路、公网页面/接口及390px布局检查通过。2675个数据文件完整备份且哈希一致，停服12.54秒，原演示数据计数保持、独立私人实例PID未变。合成验收namespace已清理，服务active且重启计数0。部署和回滚说明见 `deploy/releases/product-20261030-d64e86214b92/README.zh-CN.md`。未提交/推送。

外部限制：原有DeepSeek凭证经实际请求被服务商拒绝，真实提炼与生成问答尚不可用；连接失败与任务失败均如实显示。已启用独立管理员入口并告知用户本机密钥文件路径，已询问用户更新配置的方式；没有借用其它实例/组织密钥。

用户最终选择：先完成部署，保留当前配置。部署已完成交付；原有DeepSeek凭证失效作为已知限制保留，不再等待用户配置或继续探测。

唯一下一步：无，本轮部署收尾。以后用户明确要求修复模型配置时，再验证连接、合成提炼及带引用问答三条依赖链。

---

## 已完成：六项产品能力并行交付（2026-10-30）

目标：全部完成提炼任务中心、问答证据跳转、模型与接入管理、未决问题跟踪、完整体检队列、选择性遗忘。用户要求多会话并行；本文件保持唯一协调事实源。保留当前所有已有改动，尤其另一工作中的 eval/ 与 results/；不修改基准算法或公开数字。

可观察验收标准：
1. 提炼任务有持久待处理/运行/成功/失败、可见原因与重试，重启可恢复状态；所有入口（含结论页）按实际提炼结果提示。
2. 问答有真实证据标识、可点击事实/原文、可纠错；未决问题有持久状态和关联解决结论，列表服务端筛选分页。
3. 管理员可保存模型配置、测试连接；普通用户不能配置全局模型或读取凭证；各来源显示真实同步与失败记录。
4. 体检队列完整分页/分类/忽略与处理记录持久化；选择性遗忘按会话/来源/时间预览原文与派生数据，经明确确认后按预览一致范围执行，租户隔离、旧引用与持久化一致。
5. 六项均有后端边界回归与桌面/窄屏浏览器实际操作证据；完整离线测试、构建和zero-setup通过；两份架构文档同步。仅完成部分不标记目标完成。

协作约定：共享主文件 service.py、server/app.py、frontend lib/api.ts、hooks/queries.ts、types.ts、App.tsx、Sidebar、HANDOFF和架构文档由主会话维护。专项使用独立 management 模块、router、前端features模块和测试；模块接口见 `docs/product-completion-contract-20261030.md`。不得整文件覆盖他人修改，不推送、部署或修改真实数据。

专项会话（均为Super memory项目、本地工作树）：
- 提炼任务中心：01a1886d-a640-7333-9529-da69dbbddaca。
- 问答证据与选择性遗忘：01a1886d-c954-7fd3-805c-148116e98908。
- 未决问题与体检队列：01a1886d-e9cd-77b2-8bc2-de6fbc607418。

当前结果：六项全部本地实现并集成。完整回归744 passed / 1 skipped；最后读取恢复钩子接入后67项相关回归通过；zero-setup和前端构建通过。实际Chromium 10组功能/布局检查（1440px/390px）及7组独立服务进程重启回读通过。验证使用隔离合成数据和本地模型替身；实际LiteLLM HTTP适配器也通过本地provider成功/401测试，无付费模型调用。

交付证据：`docs/product-completion-validation-20261030.md` 和 `docs/validation/product-console-20261030/`。两份架构文档已同步。阶段闸门 PASS，1个实现候选；六项验收全部达到后停止扩展。

本地验收时边界（部署状态已由本文件顶部更新）：当时尚未部署、未提交/推送，公网为上一批 console-20261029-684b334d59c7。管理员配置需独立 ENGRAM_ADMIN_KEYS。选择性遗忘的全租户摘要/画像缓存/问题与体检处理记录重置在预览明确展示；独立原文/手动事实/配置/其它租户保留。来源状态仅为服务端收到的事件，不等于客户端在线。引用ID绑定不代表自动验证语义正确。详细边界见统一验收。

接续：已知项目Engram远端写入/关闭故障没有新的恢复证据，本轮不重复发送；确认结果保存在本交接。三个专项会话均已完成，未继续派生任务。

下一步：本轮实现目标完成，无必须继续工作。若发布这一批，按新部署授权冻结完整依赖包，并兼顾线上旧核心兼容性。

---

## 已完成：部署本轮前后端优化（Codex，2026-10-29）

用户本轮明确授权“部署上去”。目标为现有 Engram 公网服务；部署前按实际运行态核对目标，不改其它服务或私人实例。

验收：1. 冻结前后端部署包和哈希，核对线上基线及必要依赖；2. 保留可恢复代码备份，保持数据和环境配置；3. 更新后 ready/health 正常，新页面资产与接口实际回读通过；4. 核对原数据仍可读，记录部署结果与回滚路径。完成即停止。

当前结果：已部署并完成公网回读，版本 console-20261029-684b334d59c7。兼容测试38 passed / 1 skipped，公网health/ready、新接口、页面资产哈希和中文浏览器关键操作通过；390px无横向溢出，浏览器运行错误0。2675个数据文件已完整备份并校验，原演示数据计数保持，私人服务原PID保持。

部署说明与回滚入口：`deploy/releases/console-20261029-684b334d59c7/README.zh-CN.md`。本轮未提交/推送；未部署无关eval、MCP或其它本地改动。线上旧核心保持，并为默认导入语义保留兼容补丁。接续记录保存在本文件；上一阶段已确认的Engram提炼故障未变，不重复发送记忆写入。

下一步：本轮部署完成，无必须继续的动作。

---

## 已完成：前后端功能优化（Codex，2026-10-29）

范围：会话浏览、记忆筛选与来源追溯；保留工作树现有未提交改动。使用本机隔离测试数据验证，不部署、不改私人记忆库、不改变检索算法或基准结论。

验收标准（全部通过即交付）：
1. 会话索引支持分页、状态筛选与排序，选择会话可分页浏览其原文并查看提炼结果，失败/未配置模型不会显示提炼成功。
2. 事实列表支持分类、来源和排序，筛选在服务端分页前执行，页面提供准确总数与空状态。
3. 事实来源按事实 ID 精确读取，早于最近 200 条的来源可找回；账号、敏感显示与缓存保持隔离。
4. 新接口和状态边界有测试，前端构建与浏览器桌面/窄屏关键操作通过，离线 quickstart 与相关回归通过，架构文档同步。

分工：后端负责 service/app 与新增测试；前端子任务负责 Conversations 和独立会话组件；主代理负责 API/types/hooks、Facts、文档与整体验证。唯一事实源为本文件。

当前结果：四条验收全部通过。完整测试 649 passed / 1 skipped，新增10项接口回归、前端构建、zero-setup、9组真实浏览器操作通过（合成隔离数据，1440px/390px）。验证说明：`docs/console-browse-validation-20261029.md`。代码未提交/推送/部署。

接续记忆：engram_remember 返回 ReadTimeout；只读 session_report 随后确认独立会话原文1条、pending1条、facts_added=0。关闭会话返回HTTP500，未获得 outcomes_status，不能声称提炼成功。未重发，本地交接保留为当前依据。

下一步：本轮已交付；生产发布需另行明确授权。二期仅登记：将浏览器回归纳入 CI，当前不继续扩展。

---

## 历史任务：真实使用闭环验证（Codex，2026-10-25）

用户授权核验自动写入与召回是否同库，用实际会话比较有无记忆的回答效果，并修复发现的问题。本轮用户选择仅本项目接入，全局继续隔离。真实会话、密钥和原始回答仅留在仓库外的私有验证目录，不写入公开结果或测试。

验收标准（达标即停）：
1. 核对实际自动写入配置与召回配置，经服务端命名空间回读和同一会话证据证明同库，区分磁盘配置与当前客户端已加载状态。
2. 选定一段已有真实会话，经过现有采集入口入库并从召回入口找回来源；重复采集不重复入库，失败不冒充成功。
3. 冻结 3–5 个基于原始会话的具体问题和答案依据，以同模型、固定提示对比无记忆与有记忆，逐题核对正确性和来源；不把小样本结果扩展为通用基准。
4. 针对实际缺陷修复并验证相关测试、零配置示例和安装后的路径；架构/数据流变化同步中文架构文档。未核验的线上或客户端状态明确保留。

当前结果：项目级接入、原文自动写入、同库召回与 5 题历史接续已验证；代码修复已加载到本机服务。自动提炼尚受既有 provider 凭证无效阻塞，不能称全部恢复。脱敏摘要：`results/real_usage_loop_2026-10-25.json`；详见末尾本轮记录。

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

- **Agent**: Claude Code · **日期**: 2026-09-04 · **主检出整理**
- **做了什么**: 主检出从 `ed0e53a`（落后 78）fast-forward 到 `e8bfffe`。8/17 一批未提交改动原样存入本地分支 `wip/aug17-scoreboard`（未推）。其中只把 `RESULTS.md` 的 judge 端点说明合入（ARK 已下线 `deepseek-v3-2-251201`，复现命令改走 DeepSeek 官方 API）。
- **留在 wip 分支未合入及原因**: `eval/bench.py` 的 `HyMemorySystem` / `OpenVikingSystem` / `EngramLeanIdentityAttrsSystem` 适配器（17 天未随 harness 演进，需重新跑通）；`config.identity_attribute_extraction` + `IDENTITY_ATTRIBUTE_CLAUSE`（默认关，**未过 LongMemEval A/B**，按 CLAUDE.md §4 测过再合）。
- **二期**: 想启用 identity 抽取先 `--pin-extraction` 跑 500 题 A/B，看 single-session-user 类别；竞品适配器合入前先跑通一次 n=60 slice。

- **Agent**: Claude Code · **日期**: 2026-09-06 · **watch 两天零喂入的根因与修复**
- **现象**: `com.engram.watch` 自 09-04 起 115 次失败、0 次成功，日志一律 `server unreachable ... Broken pipe`，但服务 `/ready` 一直 200。
- **根因**: `ingest()` 把一个 tick 的 25 个会话打包成**单个 18.5MB 请求**；服务端 `DEFAULT_MAX_REQUEST_BYTES = 2 MiB`，读完 body 前就 413 并断开连接，urllib 报成 `Errno 32 Broken pipe`，被 `except URLError/OSError` 判为传输故障 → 退出 75、什么都不标记 → 每 30 分钟原样重来。
- **修复**: 按字节分批（`IMPORT_CHUNK_BYTES = 1.5MB`，`--max-bytes` 可调）；单批被拒只隔离该批，其余照常导入；**所有批都失败才冒泡**，保住"服务器真挂了 → 退出 75"的契约；单个超限会话按 `(size, limit)` 记账跳过，文件变大或上限提高会自动重排。
- **验证**: 全量 pytest 绿；quickstart 零 key 退出 0；对活着的 pilot 实跑 `--once --limit 6` → 6 会话 / 32 条结论 / 1 个超限已点名。撤掉分批两个新测试变红。
- **顺带修**: PR #26 里 `git checkout wip -- RESULTS.md` 用整份 8/17 旧文件覆盖了 main 版，删掉 72 行（含已发布的 `+5.6`、`84.4`），`test_results_integrity` 因此变红。已恢复 main 版并只补 judge 端点那一段。

- **Agent**: Claude Code · **日期**: 2026-09-07 · **设施指标仪表 + 会话收尾 hook**
- **目标**: 把"被依赖"定成 L0–L4 五级可证伪目标，并做成一条命令能读的仪表（Bet D 用在自己身上）。
- **第一次读数**（`python eval/facility.py --days 7`）：L0 达标（2949 条结论）；**L1 未达标**——覆盖率 77.4%（Claude Code 66.7% / Codex 80.3%），但**滞后 p50 = 3.2 天**，目标 60 秒；仪表自己判定 `watch=False hook=True`，即定时器结构上到不了 60 秒，只有 hook 能。L2 未达标（7 天窗口 33 次调用，对照组 Bash 29816 次）。L3 未达标且**0% supersession 是设计后果**（结论以 `subject=session_id` 为键，两条永不争同一槽），不是调参问题。
- **纠正了一个我一直报错的数字**：MCP 工具在会话记录里叫 `mcp__engram__engram_recall`，我此前一直 grep `engram_recall`，因此把「从未被调用」当成结论。真实全历史 171 次 / 19 个会话（162 次在其他项目，9 次自测）。仪表现在强制打印对照工具计数，且有测试锁住前缀剥离（去掉即报 0）。
- **仪表也自动发现**：写入端（watcher → 127.0.0.1:8766）与读取端（MCP → 42.193.220.197:8456）**是两个不同的记忆**，报告顶部标 `DIFFERENT MEMORY`。
- **hook**: `engram-watch --install-hook`（SessionEnd 槽），dry-run/backup/uninstall 齐备；审查修掉两个真缺陷（安装会把 settings.json 权限从 0600 放宽到 0644；符号链接的 settings.json 会被替换成普通文件）。**未在本机安装任何东西**，真实 `~/.claude/settings.json` sha256 全程未变。
- **二期**: lag 百分位的取样偏差（"文件未再增长"这道闸系统性排除了最活跃的记录）；`--roots` 不影响 backlog 计算。

- **Agent**: Claude Code · **日期**: 2026-09-08 · **决定：中立记分板不发布**
- **背景**: 8/17 跑过同 answerer/judge 的 60 题对比（`results/scoreboard_{mem0,hy}_n60.jsonl`，主检出未跟踪）：Engram 76.7%/78.3% vs mem0 20.3% / Hy-Memory 23.3%。
- **owner 判断**: "差这么多不想放了"。日志证实是对的——mem0 上下文中位 205 token（Engram 8151）、Hy-Memory 754 token 且延迟中位 1247s；**两者各 43/60 弃答**。适配器按 `topk` 取几条原子记忆，再套 Engram 自己的弃答型作答提示：对手拿到薄片 + 鼓励弃答的提示，这是系统性障碍不是能力差距。发出去会被复现打脸，自毁 Bet D。
- **发布前提（写死）**: 每个竞品适配器先在**对方自己的 benchmark** 上复现出对方自报数字的量级；复现不出即视为我们的适配器 bug。在此之前这些日志只是内部诊断，**任何 agent 不得把它们写进 README / RESULTS / 官网**。
- **不变**: 追总分的路径仍是压缩层那 9.4 分（`COMPARISON.md` §4）与全量 LOCOMO；不靠对比对手。

---

- **Agent**: Codex · **日期**: 2026-10-25 · **真实使用闭环验证与修复**
- **授权边界**: 用户明确选择仅本项目接入。安装项目 `.codex/config.toml` 与 `.mcp.json`，共用既有私人本机服务和 watcher 的凭证文件；运行配置加入本地 Git exclude，没有加入全局 Engram 配置、没有变更公网实例。
- **实际故障及修复**:
  1. 无可读会话的压缩/元数据 JSONL 反复占满 watcher 限额。新增按 size 的 `non_conversational` 记录，增长后重新排队，读取损坏不忽略；fed 只计真正完成的会话。
  2. 服务把提炼模型异常吞成空数组，watcher 错认成功。新增 `outcomes_status` 与安全异常类别；三次失败进入 `close_deferred`，不标 seen。provider 以 HTTP 400 返回认证错误时快速失败，避免无效重试。
  3. MCP 不支持 watcher 的 key file，且远端纯召回会额外调用 answerer。MCP/setup/doctor 增加 key-file 支持；HTTP recall 默认保留控制台回答行为，MCP/doctor 显式 `answer=false`。hook 去重同时核对 target。
- **运行回读**: 两份项目配置分别启动实际 MCP 进程，服务端 user 与全部计数一致；Codex CLI 在本项目读到 enabled 的 Engram。服务重启后 `/ready` 恢复 200，已有数据保留。实际调度器一批尝试 25 个文件：23 个无可读对话文件单独跳过、1 个原文 episode 入库、2 个提炼失败明确保留；下一轮队列不再含这 23 个同大小文件。真实来源重复导入两次均 skipped=1、episodes=0。
- **回答对照**: 先冻结 5 个历史问题及原文依据，再用两个独立新上下文的同一代理模型作答。无记忆组 5 题均无法确认；记忆组 5 题均有原会话支持，包括正确区分工程交付与算法增益。模型只接收问题与 MCP 召回内容，不拿标准答案；源记录另行逐题核对。只有一个历史会话的定点证据，不代表普遍准确率或用户效用验收；未用失效 provider 冒充成功生成。
- **验证**: 完整离线测试 639 passed / 1 skipped；zero-setup quickstart 通过；架构地图、完整架构报告和接入文档同步。代码未提交/推送。当前聊天未验证 MCP 热加载；新进程与 CLI 读取已通过。
- **唯一外部阻塞**: 既有 DeepSeek API 凭证被 provider 明确判 invalid。已请用户在本机 `.env` 更新 `DEEPSEEK_API_KEY`，不在聊天提供密钥；本轮未替换提供方或寻找其他业务账号凭证。原文存储和纯召回可用，自动提炼仍未恢复。
- **接续动作**: 用户更新凭证后，只做一次最小连通调用，重启本机服务，重跑指定来源的 close 并回读 `outcomes_status=completed`/来源报告；按已保存失败条目定向重试，不全库重抽取。重新加载本项目 MCP 后检查实际聊天可见工具，不因此修改全局配置。


---

## 2026-10-29 算法世界领先目标（Codex，当前阶段）

- 用户目标：算法成为世界第一。目标保持有效；排名必须限定基准、同条件配置、成本与日期，不把工程通过当作领先证据。
- 当前阶段：核验现有完整日志与主要失分，冻结第一轮可重放对照，最多两个机制候选。
- 验收标准：①当前完整基线的准确率、tokens、p50/p95 与 full-context 可追溯；②根据真实失分和源码选定机制，记录冻结对照与污染边界；③若实现候选，给出真实数据对照及离线回归，算法文档同步；④明确全量评分与世界排名的剩余证据缺口，不改公开数字。
- 约束：保护所有现有未提交改动；不部署、不公开发布、不操作私人记忆库中的业务数据、不借用其他项目凭据；若模型不可用，继续离线可判定工作并保留真实阻塞。
- 阶段台账：`.codex-stage-gate/algorithm-20261029.json`。
- 首个候选：独立正分 BM25 候选与dense候选做RRF；这是候选扩展与零分不投票的复合改变，不分别归因。固定模型、500题、k=2/4/8/28，原查询不读gold；完整覆盖、任一覆盖、比例、字数与检索时延共同报告。
- 预注册晋级：优先k=4/8；若全部证据覆盖未有净改善，或任一目标k退步，则不启用生产默认。若有改善仍仅晋级到固定抽取的最终context/QA配对，不能凭单步召回提升宣布算法总分提升。后续候选需新的机制证据。
- 已知外部条件：2026-10-29三项既有bench模型最小请求失败（DeepSeek BadRequestError，ARK回答/抽取 AuthenticationError）；恢复在线QA需本项目凭据可用，不转用其他项目账号。
- 已排除的直接改法：numeric候选按不同句子保留每次出现。33个相关题中12题存在同键折叠；精读3例均为同一游戏时长/同一活动费用/同一本书页数的重复提及。合成“两事件同额同日”可复现风险，但未确证本轮真实独立事件误折叠，不凭合成例子修改默认去重。
- 回归：新增评测相关23测试通过；完整离线pytest退出0，quickstart退出0。
- 最新指示（2026-10-30）：用户选择“先完成离线实验”。本轮完成500题离线对照后交付结论，不启动后续在线QA。用户回复前发出的同项目替代模型最小探测返回502；不重试或更改项目模型配置。
- 当前阶段已交付：500行/500唯一qid，470召回题、30弃答排除，源码指纹和完成后resume均通过。k=4全证据390→397（8胜1负），k=8为436→438（3胜1负）；k=4 p50 59.7→134.2ms。dense池外gold新增返回为0，收益发生在池内排序；保留实验实现，不改生产默认。结果见 `docs/algorithm-evidence-20261030.zh-CN.md`，逐题日志和manifest见 `results/evidence_coverage_full500_20261029.jsonl*`。
- 总目标未完成；本轮按用户选择止于离线实验。下一阶段唯一建议：固定抽取与证据映射，追踪候选→摘要→最终context的标注证据保留，再在可用项目模型及明确成本下做完整QA对照；不要再用任一会话命中率判定证据完整。


### 2026-10-30：离线证据保留阶段

- 上轮分类：progress。完整500题证据改变了行动：主查询top28已覆盖469/470完整标注会话，继续扩大候选池缺少依据；转向固定抽取后的上下文保留。
- 阶段边界：用户选择先离线；不调用任何在线LLM、不重试凭据、不部署；当前并行产品改动全部保留。
- 验收：①缓存缺失/损坏不能被rule fallback冒充有效重放；②同题同缓存、固定选择及实现指纹；③明确会话来源保留与语义答案保留的差别；④根据实际损失支持候选或记录可复现阻塞，不能把任意文本重合当QA正确。
- 首探针：用现有pinned extraction缓存和本地BGE向量重放一题完整lean_context；不读/生成新测试答案。
- 阶段台账：`.codex-stage-gate/algorithm-context-20261030.json`。
- 唯一下一步：确认现有缓存能否完整重放真实context，缺失则先定位原因而不发起网络补全。
- 缓存探针已通过：首题 37 次缓存命中、0 缺失；未构造在线 provider。合成反例确认原文进入 detail 集合后摘要提前去重、随后原文被预算裁掉的双重丢失。候选 `packing_summary_fallback=False` 默认关闭，只为未完整显示的原文回补已有摘要。
- **真实结果读取前预注册**：从 LongMemEval_S 中六类可回答且有标注会话的题目，各按 `SHA256("context-packing-v1:" + qid)` 取前 10，共 60 题；不读答案挑题、不补无效题。一次构建冻结 blocks/need/detail episodes，两臂从同一冻结输入装配，主预算 60,000 字符，32,000/16,000 为次级压力诊断。缓存缺失即整题无效，即使生产代码吞错 fallback 也不计有效。
- 晋级标准：主预算必须存在真实来源完整性恢复，且逐题完整来源覆盖与已存在的短答案字面诊断均无回退，才保留为后续 QA 候选；仅次级预算改善或仅合成改善不晋级。来源完整性与短答案字面匹配均不等于 QA 正确，任何结果都不自动打开默认开关。冻结重放必须不依赖模型、embedding 或重新检索，并验证源码指纹。
- 第一候选已完成：60/60 有效、2,259 次缓存命中、0 缺失；纯冻结重放 JSONL 逐字节相同。60k 两臂均保留 98/100 标注来源、58/60 题完整来源、20/33 短参考字面；32k/16k 亦无来源或字面收益，未通过晋级。主预算仅 7 题触发裁切，不能推成机制普遍无效。
- 新机制证据：`results/context-replay-20261030.losses.json` 定位 `6a27ffc2` 回补摘要被追加到最终摘要配额以外，因而二次裁掉；`6456829e` 原摘要末部被裁，未进入 detail，不属当前回补范围。另一个字面缺失在摘要生成时已发生，不能靠 packing 恢复。
- **第二候选运行前预注册**：仍使用原 60 题冻结输入、原三档预算和原晋级标准，只把现有 detail 回补摘要放到 summary 正文前部，以检验二次裁切机制；不改检索/抽取、不读答案/gold 决策。第二候选限于 eval 原型，不修改现有 core/manifest 依赖，不新增样本，不再扩大候选。本阶段最后一个候选完成后合并报告所有结果；任何改善仍须后续 QA 验证。
- 第二候选已完成并冻结：`eval/context_replay_priority.py` 仅前置已有回补摘要。60k 来源 98→99/100、全来源题数 58→59/60、短字面 20/33 不变；32k 来源 37→74/100、字面 17→18/33；16k 来源 3→40/100、字面 9→11/33。所有预算逐题均无标注来源/短字面回退，但挤占了部分非标注摘要，不能推断真实 QA 无回退。第一/二候选各自纯重放字节一致，第二候选 baseline 三预算逐题哈希与第一运行一致。
- 当前交付：`docs/algorithm-context-evidence-20261030.zh-CN.md` 及 `results/context-replay-20261030*`。完整离线 pytest、零密钥 quickstart、最终相关 224 项测试通过。唯一独立核验已用于第一候选数据与评测设计，第二候选由实现自测和根代理核对，不追加审计套审计。core 开关仍是失败的第一版且默认关闭，第二版只在 eval，不启用生产默认。
- 当前唯一下一步：封存第二候选与原 60 题选择，按现有用户“先离线”边界，在未参与诊断的其余 410 道可回答题做固定候选验证；不增加新算法或调参。缓存缺失显式记 invalid、不联网补齐。之后再决定是否值得恢复项目模型做完整 QA；未达到世界第一，不改总目标为完成。

### 2026-10-30：固定候选的其余 410 题验证

- 上轮分类：progress。已有真实来源收益及可重放原型，下一步从开发样本转向固定的剩余样本；不重开候选搜索。
- 开始时已核验原 8 份源码、候选源码及两个原 manifest。预注册在 `results/context-holdout-20261030.preregistered.json`，410 题与开发 60 题不相交；这是同一个既有公开基准的剩余题，不宣称新独立 benchmark。
- 验收：①完整尝试固定 410 题，invalid 不替换、不隐藏；②沿用同一个候选及 60k 主预算，32k/16k 仅次级诊断；③每题两臂使用同一冻结输入，缓存缺失不得联网补齐；④纯重放结果与原运行一致，按真实分母报告标注来源/短字面/字符与 helper 时延；⑤主预算有来源恢复且无逐题来源/短字面回退才保留待 QA，任何结果均不自动启用生产默认。
- 阶段台账：`.codex-stage-gate/algorithm-context-holdout-20261030.json`。本阶段不修改算法、不扩充候选、不基于结果调参；保留其他任务改动。
- 固定验证已完成：进程 18719 退出 0；410 唯一题全部尝试、407 有效、3 缺缓存。原 60 与 410 完全互补，未替换样本。15,464 缓存命中、3 未命中；15,160 唯一已用文件的内容哈希核验通过。3 个缺失 key 在当前指定缓存及项目 `results/`、`data/` 的精确文件名搜索中均不存在，未补请求或自动重试。
- 主预算结果：全来源题数 363→387/407、完整标注来源 736→764/786，28 题各救回一个来源，未观察到主预算标注来源/短字面回退；短字面仍 117/260。32k/16k 分别 13/7 题失去原有完整摘要来源，净增不能掩盖这些回退。
- 重放与验收：原运行和纯重放 411 条 JSON 解析后逐条相同；并非逐字节相同，3 条 invalid 仅 JSON 字段顺序不同，所有有效输入和 context 哈希一致。新 runner 9 测试、报告门槛 5 测试通过；本阶段唯一独立核验已完成新 runner 和预注册的只读检查。完整检查见 `results/context-holdout-20261030.checks.json`，交付见 `docs/algorithm-context-holdout-20261030.zh-CN.md`。
- 裁决：`inconclusive_incomplete_cache`。保留研究原型，不启用默认、不修改候选、不以来源/字面指标宣称 QA 提升或世界第一。
- 最新用户指示：离线完成后用户明确回复“暂不恢复在线评测”。不重试凭据、不启动真实 QA、不再询问同一事项；保留当前结果。
- 阻塞核对：真实 QA 所需的项目模型/在线评测条件在原始目标轮及连续两轮目标续跑中均未恢复；前两轮仍有离线工作，本轮已完成预注册剩余验证。当前缺失的是固定回答模型、固定裁判、包含弃答题的 QA、实际 tokens 和端到端时延及可比外部结果；不能再用离线代理指标替代。恢复条件是用户明确恢复在线评测且本项目模型凭据有效；总目标未完成。
