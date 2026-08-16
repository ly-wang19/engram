# 分支债务甄别台账（2026-08-16）

## 为什么有这份文档

仓库长期存在"能力写完并测过、但从未合入主干"的债务。记忆账本和交接记录里这些能力被标记为
`shipped`，但主干上并不存在——**"写完并测过" ≠ "主干有"**。本文件记录一次逐项甄别的结论，
避免后续 AI 或人类重新推导，也避免把已被主干超越的旧实现硬合回来。

甄别基线：`8decc8c`（= `ed0e53a` release 0.1.0 + cross-account），439 passed / 1 skipped。

## 甄别方法（重要）

**按能力比对，不是按文件名比对。** main 在 2026-06 之后独立演进了 100+ 个 commit，同一能力
经常以不同文件名和不同设计存在。硬 `git merge` 会把主干已经更好的实现覆盖掉。

每一项的判定必须给出主干侧的 `文件:行号` 证据。

## 分支状态

| 分支 | 相对 main | 时间 | 处置 |
| --- | --- | --- | --- |
| `claude/cross-account-memory-module-95f184` | +1 / −0 | 2026-08-12 | ✅ 已 fast-forward 合入（+22 测试） |
| `claude/sweet-haibt-7231c5` | +1 / −102 | 2026-06-10 | 逐项移植，见下表 |
| `claude/goofy-shockley-f70706` | +11 / −103 | 2026-06-08 | 逐项移植，见下表 |

## A3 — `sweet-haibt-7231c5`

| 能力 | 主干现状 | 建议 | 风险 |
| --- | --- | --- | --- |
| ANN 候选生成（`Config.ann_candidates`/`ann_pool`） | ❌ 无。`engram/retrieve/hybrid.py` 每次查询全量扫描 `fact_store.values()` 与 `graph.entities.values()` | **重写实现同等能力**（宪章 Bet E 承重墙） | 中 |
| async System-2（`ENGRAM_ASYNC_CONSOLIDATION`） | ❌ 无 | 移植 | 中（`service.py` 已大改） |
| multi-Space（`engram/spaces.py`） | ❌ 无 | 重写（`MemoryService` 已是 namespace-keyed，主要是读融合 + ACL 层） | 中 |
| 文档摄入 PDF/DOCX + 图片 caption | ❌ 无 | 移植（可选 extra，独立于检索路径） | 低 |
| embedding 空间版本守卫 | ✅ **主干更强**：`engram/store/persist.py:36,238` 的 manifest 同时校验 `embedder_id` 与 `embedding_dim` | **跳过守卫本身** | — |
| └ 但缺 `reembed()` 迁移 | ❌ 主干只能抛 `EmbedderMismatchError`，无法从源文本重建向量 | 补迁移路径 | 中 |
| import 幂等 | ✅ cross-account 合入时已带来（按 id 幂等） | 跳过 | — |

## A4 — `goofy-shockley-f70706`

| 能力 | 主干现状 | 建议 | 风险 |
| --- | --- | --- | --- |
| `store/snapshot.py`（SQLite 快照） | ✅ **主干更强**：`engram/store/persist.py` 用 JSONL + manifest + 原子 tmp→final + 文件锁 + committed-prefix 校验 + embedder/dim 守卫；另有 `store/migrate.py` 迁移旧 pickle | **丢弃分支版** | — |
| `server/keys.py`（自助签发 API key） | ⚠️ 部分：主干 `engram/server/app.py:48,183` 有 `ENGRAM_API_KEYS` 静态映射 + `hmac.compare_digest` 常量时间比较 + 严格 Bearer 解析。分支版额外提供自助签发、只存 hash、吊销、列表（101 行） | 有增量价值，中优先级 | 低 |
| `server/ratelimit.py`（49 行） | ❌ 无 | 移植 | 低 |
| `server/idempotency.py` | ❌ 无 | 移植 | 低 |
| `server/metrics.py` | ❌ 无 | 移植 | 低 |
| `engram/client.py`（Python SDK） | ❌ 无（只有 TS 客户端） | 移植 | 低 |
| `store/crypto.py`（Fernet 落盘加密） | ❌ 无 | 移植（需可选 `cryptography` 依赖） | 中 |
| `store/pg_store.py`（pgvector） | ❌ 无（`store/base.py:2`、`server/app.py:19` 只在文档里提到） | **暂缓** — 该代码从未用真实数据库验证过，属未验证代码，不应盲目并入 | 高 |
| `integrations/`（LangChain / LlamaIndex retriever） | ❌ 无（主干有 `docs/agent-adapters.md`，但那是 MCP/跨 Agent 适配，不是框架 retriever） | 移植（需可选依赖） | 低 |

## 未提交工作（不在任何分支上）

13 个 worktree 中有 5 个含**从未提交**的改动，合计 2000+ 行。这些不在任何分支上，删除 worktree
即永久销毁：

| worktree | 未提交内容 |
| --- | --- |
| `wonderful-tereshkova-b63377` | `retrieve/router.py`(140) + `retrieve/segment.py`(131) + 两个测试(189) + 167 行 diff |
| `vigilant-mahavira-73886f` | `retrieve/recall_pipeline.py`(253)，无测试 |
| `laughing-tesla-360ff1` | `TECHNIQUES.md` + `docs/{README,console,evaluation,governance,memory-policy,moat}.md` 共 730 行 |
| `cool-chebyshev-aa0347` | consolidate 三模块改动 72 行 + `tests/test_profile.py`(40) |
| `nostalgic-clarke-dda32e` | 480 行 diff |

### 处置结果（2026-08-16）

隐私前置条件已由仓库所有者确认：`zhangyuwei` 为**化名**；另对该语料扫描了邮箱、手机号、证件号与
账号类标识，**未发现**其它可识别信息，满足 `CONTRIBUTING.md` 的提交门槛。

全部未提交工作已**按原样提交到各自分支保全**（每个 worktree 现为 main+1、工作区清空）。保全 ≠ 合并：
这些都是 2026-06 的代码，主干此后走了 100+ 个 commit，需逐个对照今日主干重新验证后才能移植。

| worktree | 保全提交 | 判定 |
| --- | --- | --- |
| `wonderful-tereshkova-b63377` | `c6912df` | **部分仍有效**。`segment.py` 记录的缺陷**今天仍在**：`retrieve/rerank.py` 的 cross-encoder 仍是 `max_length=512` 且无分段，重排 ~2000 token 的 session 会静默只对前 512 token 打分（实测 70.0%→57.5%）。主干 rerank 默认关闭，限制了影响面但没有修复。`router.py` 的按查询路由与优化地图上仍未关闭的 P2「runtime profiles」高度重合 |
| `vigilant-mahavira-73886f` | `21e7baa` | **未被覆盖**。把上下文拆成可缓存的 STABLE 块（进 system prompt）与每轮变化的 DYNAMIC 块，主干 `lean_context` 至今仍拍平成单个字符串。同样证据、更少重复计费——属 Bet A 三联表里 tokens/latency 那两维。无测试 |
| `sweet-haibt-7231c5` | `d3f4f70` | **真实缺口**。`engram/metrics.py`（99 行）+ 测试（119 行）+ `/metrics` 端点，主干至今没有可观测层。纯 stdlib、只暴露聚合量（不含命名空间名与查询文本） |
| `laughing-tesla-360ff1` | `103359c` | 730 行文档。主干此后自建了文档体系，重合度未知，且其中数字未按「每个公开数字可追溯到已提交日志」的规则复核过 |
| `cool-chebyshev-aa0347` | `7ee1123` | consolidate 三模块改动 + `tests/test_profile.py`。主干此后重写了大部分 consolidation，需重新对照 |
| `nostalgic-clarke-dda32e` | `2ccfe15` | 车载记忆演示语料 + 控制台接线。`memory.py`/`server/app.py`/前端在主干均已大改 |

**未提交但未保全的**（判定为可再生或无价值，故意留在原处）：
`distracted-tereshkova` 的 `paper/main_anon.{pdf,bbl}`（匿名构建产物，可由已提交的开关重新生成）、
`hardcore-nobel` 的 `frontend/package-lock.json`（前端与 CI 用 pnpm，该 npm 锁文件是残留）、
`sharp-chandrasekhar` 与 `peaceful-almeida` 的 `TECHNIQUES.md`（与 `103359c` 中已保全的同名文档重复）。

**main 工作区的未跟踪文件未处理**：`IDENTITY.md` / `SOUL.md` / `USER.md` 和 11 个
`results/*.jsonl`。后者是实验日志，按 Bet D「每个公开数字可追溯到已提交日志」的规则**可能应当提交**，
但提交到 main 属于仓库所有者的决定，未擅自执行。

## 结论

1. 分支上的东西**不都是财富**。持久化层是反例：主干的 `persist.py` 明确优于分支的 `snapshot.py`。
   任何"把旧分支合回来"的动作都必须逐能力甄别。
2. 主干真实缺口按价值排序：**ANN 候选生成**（宪章 Bet E）→ 服务层加固三件套
   （ratelimit / metrics / idempotency）→ Python SDK → async System-2 / multi-Space →
   文档摄入 → 加密 → 框架适配器。
3. `pg_store.py` 属未验证代码，在拿到真实数据库前不并入。
