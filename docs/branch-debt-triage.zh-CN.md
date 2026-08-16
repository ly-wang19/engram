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

⚠️ **隐私**：`nostalgic-clarke-dda32e` 含 `examples/seed_zhangyuwei.py` 与 `examples/zhangyuwei_memory.md`，
疑似真实人名。`CONTRIBUTING.md` 禁止提交真实个人数据与姓名——这两个文件在确认为化名前不得提交。

⚠️ **不要盲目抢救**：这些均为 2026-06 的工作，主干此后新增了 `retrieve/evidence.py` 与
`retrieve/aggregate.py`。`router.py` / `segment.py` / `recall_pipeline.py` 很可能已被覆盖，
需逐个判定"仍有价值 vs 已过时"。

## 结论

1. 分支上的东西**不都是财富**。持久化层是反例：主干的 `persist.py` 明确优于分支的 `snapshot.py`。
   任何"把旧分支合回来"的动作都必须逐能力甄别。
2. 主干真实缺口按价值排序：**ANN 候选生成**（宪章 Bet E）→ 服务层加固三件套
   （ratelimit / metrics / idempotency）→ Python SDK → async System-2 / multi-Space →
   文档摄入 → 加密 → 框架适配器。
3. `pg_store.py` 属未验证代码，在拿到真实数据库前不并入。
