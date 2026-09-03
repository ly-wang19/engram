# Embedder 选型：线上默认在中文上等于随机

**日期** 2026-09-01 ｜ **动机** 线上实例用 `HashingEmbedder`，owner 的记忆几乎全是中文。

## 证据 1：真实记忆文档（16 篇，owner 的 md 记忆）

对 3 个查询取 top-1，看是否命中应该命中的那篇：

| embedder | 命中 | 最高/最低分差 |
|---|---:|---:|
| `bge-small`（英文预设，benchmark 在用） | **0/3** | +0.12 |
| `bge-small-zh` | 2/3 | +0.16 |
| **`multilingual`** (paraphrase-multilingual-MiniLM-L12-v2) | **3/3** | **+0.30** |

`bge-small` 的三个查询 top-1 是**同一篇**不相关文档 —— 它没在排序，只是返回了一个固定答案。

## 证据 2：真实已存事实（88 条，`/v1/recall` 实际检索的单元）

| embedder | 区分度（top1 − 末位） |
|---|---:|
| `hashing`（**线上默认**） | **+0.000**（每一条都是 0.000） |
| `bge-small` | +0.19 ~ +0.21 |
| `multilingual` | **+0.35 ~ +0.56** |

线上 `/v1/recall` 实测：**三个完全不同的查询返回一模一样的四条事实**。这不是排序不准，是排序不存在。

## 结论

- 服务端默认改为 `multilingual`；新增预设 `multilingual` 和 `bge-small-zh`。
- **benchmark 不受影响**：`eval/bench.py` 通过 `--embedder` 显式传参，LongMemEval 是英文语料，继续用 `bge-small`。
  已发布的数字（84.4 / +5.6）不动。
- 迁移：`ENGRAM_EMBEDDER=multilingual ENGRAM_REEMBED_ON_MISMATCH=1`（重嵌入路径已存在）。
- 成本：模型 458MB（vs bge-small-zh 92MB）。线上机 3.7G 内存 / 7.4G 磁盘（94% 满），部署前需确认余量。

## 同时暴露的更大问题（未修）

88 条事实里 **84 条谓词是 `occupation`**、4 条 `lives_in`。规则抽取器把所有中文内容硬塞进两个传记槽位，
产出 `real occupation "借鉴大厂"`、`NOT occupation below the noise` 这类无法阅读的残句。

**换 embedder 只是让排序恢复工作，被排序的内容本身仍是垃圾。** 这是比 embedder 更靠前的瓶颈，
也正是 `engram/consolidate/outcomes.py` 存在的理由（见其模块文档）——但 outcomes 层当前默认关闭
（`Config.session_outcomes = False`），且前端无任何入口。
