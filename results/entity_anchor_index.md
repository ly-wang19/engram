# entity_anchor_index — 实体锚定索引的扩展性测量（2026-08-16）

## 背景

`HybridRetriever.query_entity_ids()` 是每次检索都会走的路径，用于判断查询提到了哪些实体
（图扩展的锚点）。它此前遍历 `graph.entities.values()` 全部实体，对每个实体做名称/别名匹配。

`InMemoryGraphStore` 现在维护 `(user_id, 词干) -> 实体 id` 的倒排索引，检索时只查**查询自身的词**。

## 复现

```bash
python3 - <<'PY'
# 见本文件末尾脚本；或直接用 tests/test_entity_index.py 验证等价性
PY
```

等价性由 `tests/test_entity_index.py::test_indexed_and_scanned_anchoring_agree` 保证：同一组实体
分别装入带索引和去掉索引查找方法的图存储，用**真实的 HybridRetriever** 跑同一批查询，两条路径
必须给出完全相同的锚定结果。索引只允许更快，不允许更不一样。

## 结果

**有区分度的实体名（真实场景：人名、地名、公司名）**

| 实体数 | 全扫描 | 索引 | 加速 |
| ---: | ---: | ---: | ---: |
| 500 | 0.735ms | 0.004ms | 183.7x |
| 2000 | 3.027ms | 0.004ms | 756.7x |
| 10000 | 16.052ms | **0.004ms** | **4055.6x** |

索引耗时**与实体数无关**——成本随查询词数走，不随库大小走。这是该路径第一次真正脱离 O(n)。

**共享高频词的实体名（对抗场景：全部实体都叫 `entity number {i}`）**

| 实体数 | 全扫描 | 索引 | 加速 |
| ---: | ---: | ---: | ---: |
| 500 | 0.810ms | 0.492ms | 1.6x |
| 2000 | 3.351ms | 2.102ms | 1.6x |
| 10000 | 17.029ms | 12.041ms | 1.4x |

## 结论与边界（重要）

**索引的收益取决于实体名的区分度，不是无条件的。** 当所有实体名共享同一批高频词时，这些词的
倒排表长度等于全库，候选集就是全部实体，索引退化为"全扫描 + 一次索引查找"，只剩常数因子收益。

真实实体名（人名、地名、机构名、产品名）几乎总是有区分度的，所以真实场景是主导情形。但这个边界
必须写明：这不是一个"任何情况下都 O(1)"的优化，而是一个"在词分布正常时 O(查询词数)"的优化。

第一版基准曾用 `entity number {i}` 作为合成实体名，测出只有 1.5x 且仍然线性——那是基准数据落进了
退化情形，不是实现有问题。记在这里，避免以后有人用同样的合成数据重新得出"这个索引没用"的错误结论。

## 仍然线性的部分

`graph_excluded_entity_ids()` 在查询**确实含否定词**时仍遍历全部实体。它按子串匹配（中文实体名
必需，词元化看不见），无法用词元倒排索引替代。参见 `results/bounded_candidates_scaling.md`。

## 测量脚本

```python
import time, statistics
from engram.config import Config
from engram.embed.hashing import HashingEmbedder
from engram.retrieve.hybrid import HybridRetriever
from engram.store.memory_store import InMemoryGraphStore, InMemoryVectorStore
from engram.types import Entity

class Unindexed(InMemoryGraphStore):
    entities_by_terms = None  # forces the retriever's full-scan fallback

def bench(cls, n, namer, query):
    g = cls()
    for i in range(n):
        g.upsert_entity(Entity(user_id="u1", name=namer(i)))
    r = HybridRetriever(InMemoryVectorStore(), g, HashingEmbedder(), Config())
    r.query_entity_ids(query, "u1")  # warm
    s = []
    for _ in range(60):
        a = time.perf_counter()
        r.query_entity_ids(query, "u1")
        s.append((time.perf_counter() - a) * 1000)
    return statistics.median(s)

# distinctive: lambda i: f"zeta{i} corp{i}"      query "tell me about zeta42"
# shared:      lambda i: f"entity number {i}"    query "tell me about entity number 42"
```
