# bounded_candidates — 读路径扩展性测量（2026-08-16）

## 复现命令

```bash
python3 eval/scaling.py --sizes 100,500,2000,10000 --trials 15 --pool 400
```

离线、确定性（HashingEmbedder + 合成事实），无 API key。测的是**成本曲线的形状**，不是生产绝对延迟。

## 结果

| 事实数 | 全量扫描 p50 | bounded 含语义通道 | bounded 去语义通道 |
| ---: | ---: | ---: | ---: |
| 100 | 2.07ms | 3.82ms | 0.62ms |
| 500 | 8.71ms | 15.64ms | 2.43ms |
| 2000 | 33.13ms | 43.41ms | 8.54ms |
| 10000 | 177.88ms | 177.03ms | **12.28ms** |

每千条事实的毫秒数（恒定 = O(n)，下降 = 次线性）：

| 变体 | 100 | 500 | 2000 | 10000 |
| --- | ---: | ---: | ---: | ---: |
| 全量扫描 | 20.67 | 17.42 | 16.57 | 17.79 |
| bounded 含语义通道 | 38.18 | 31.29 | 21.70 | 17.70 |
| bounded 去语义通道 | 6.17 | 4.86 | 4.27 | **1.23** |

存储增长 100x 时，单查询成本增长：全量扫描 **86.0x** / 含语义通道 46.4x / 去语义通道 **19.9x**。

## 结论

1. **主干读路径是 O(n)。** 每千条事实的耗时在 100→10000 区间恒定在 ~17–20ms，这是全量扫描的
   特征。10000 条事实时单查询已达 177ms，超过宪章的 <100ms 读路径目标，而这个规模对
   "10M+ token" 的目标而言还很小。
2. **只把词汇与融合环节收敛成候选池，收益为零**（177.03ms vs 177.88ms）。因为语义通道调用
   `fact_store.search()`，而两个后端都没有真正的 ANN 索引：`InMemoryVectorStore.search()` 是
   暴力 cosine 加一次全量排序；`LanceDBVectorStore.search()` 一旦传入 Python 谓词就走
   `table.to_arrow().to_pylist()` 全表物化。多租户检索每次都必须带 user 过滤，所以线上路径
   **永远拿不到 ANN 收益**。
3. **候选池设计本身是有效的。** 去掉语义通道后 10000 条事实上快 14.5x，且每千条耗时从 6.17
   降到 1.23——真正的次线性。卡住 Bet E 的是缺失的 ANN 索引，不是候选池思路。

## 追加：规模后端的租户过滤下推（同日）

上面第 2 条指出的根因已修复。`user_id` 从不透明的 JSON payload 中提升为 LanceDB 的真实列，
`VectorStore.search()` 增加声明式 `user_id=` 参数（与通用 Python 谓词并存），多租户检索因此
可以走 `where(..., prefilter=True)` 在索引内部收窄。

| 行数 | 下推 prefilter | Python 谓词 | 加速 |
| ---: | ---: | ---: | ---: |
| 500 | 1.13ms | 4.04ms | 3.6x |
| 2000 | 1.16ms | 15.18ms | 13.1x |
| 10000 | 1.35ms | 75.38ms | 56.0x |
| 40000 | 1.83ms | 302.36ms | **165.4x** |

行数增长 80x（500→40000）时，下推路径延迟只从 1.13ms 涨到 1.83ms——**基本是平的**，而谓词路径
是严格线性的。这就是 Bet E 在规模后端上的兑现。

正确性由 `tests/test_lancedb_tenant_filter.py` 保证，其中
`test_prefilter_finds_hits_beyond_the_unfiltered_neighbourhood` 是专门设计来证伪"过滤发生在
ANN 之后"的：让多数租户的行填满查询的整个最近邻域，少数租户的行全部远离查询。若过滤是后置的，
top_k 里一条目标租户的行都没有，返回空。该测试通过，说明 prefilter 真实生效。

向后兼容：旧版本写出的表没有 `user_id` 列。`_has_tenant_column()` 按真实 schema 探测而非假设，
旧表继续可读可写（退回扫描），不会因 schema 不匹配而报错或损坏数据。

## 追加：否定约束的提前返回 + 按键读取下推（同日）

**否定约束提前返回。** 每次检索都会走 `query_entity_ids()`，它末尾调用
`graph_excluded_entity_ids()`，后者对**每个实体名**跑两遍正则。而绝大多数查询根本没有否定词。

`_EXCLUSION_BEFORE_RE` 用 `\s*$` 锚定在"实体名之前那段文本"的末尾，不能直接拿来搜整个查询；
但"查询中至少出现一个提示词"是它匹配的必要条件，据此可以提前返回。

5000 个实体时实测：

| 查询 | 耗时 |
| --- | ---: |
| 无否定词（`where does alice work`） | **0.0009 ms** |
| 含否定词（`anywhere except entity number 12`） | 508.94 ms |

**一个被测试抓到的真实缺陷。** 提示词正则最初写成 `\bnot\b`（与真正的匹配器一致），这是错的：
`before` 切片终止于实体名起始处，而非 ASCII 实体名在 `_entity_name_mentions` 里**不带词边界守卫**，
所以 `"not上海"` 的切片 `"not"` 末尾构成词边界，而完整串里 `"not上"` 两侧都是 `\w`、提示词正则
匹配不到——会静默丢掉一次排除。提示词正则因此去掉尾部 `\b`，故意比真正的匹配器更宽松：多扫一次
只是白跑，漏扫则是错误。`tests/test_exclusion_shortcut.py` 对样本的**每一个前缀**验证
"锚定匹配器命中 ⟹ 提示词匹配器命中"这一不变量，未来若只给其中一个正则加提示词会立即失败。

**按键读取下推。** `LanceDBVectorStore.get()` 此前全表物化后在 Python 里线性找 key，使任何
逐 id 访问变成二次复杂度。改用纯过滤查询 `table.search().where("key = ...")`，谓词在 LanceDB
内部执行。（`pylance` 未安装、`table.query()` 在 0.33 不存在，实测确认 `search().where()` 可用。）

## 仍然存在的热点

- `graph_excluded_entity_ids()` 在**确实含否定词**时仍是 O(实体数)，5000 实体需 509ms。提前返回
  只是让它不再影响绝大多数查询，没有解决这条路径本身。它按子串匹配（中文实体名必需），无法用
  词元倒排索引绕开。
- `query_entity_ids()` 的名称匹配与别名锚定仍扫全部实体。这两处是词元化的，**可以**建倒排索引。
- `InMemoryVectorStore.search()` 仍是暴力 cosine——参考实现设计如此，模块文档已写明适用到 ~10k。
- `values()` 按接口语义必须返回全部，无法下推。

## 未采用/暂缓原因

- `candidate_vector_channel=False` **不作为默认**：它会丢掉"语义相关但与查询无共享词"的事实，
  这正是 M1 已验证的 hybrid 论点所依赖的召回。在拿到真正的 ANN 后端、或有 keyed harness 跑出
  召回损失可接受的证据之前，默认保持召回安全。
- `bounded_candidates` **默认关闭**：已发布数字由全量扫描产出，静默改变打分集合会违反
  "每个公开数字都可追溯到已提交日志"的规则。候选池 ≥ 存活事实数时两条路径逐位一致
  （`tests/test_bounded_candidates.py::test_bounded_matches_full_scan_when_pool_covers_store`）。

## 下一步（按依赖顺序）

1. 给向量存储一个真正的过滤 ANN：LanceDB 侧把 user 过滤下推成 SQL 谓词（而不是 Python 回调），
   即可让 `search()` 走真实索引。这是解锁上面 14.5x 的唯一前提。
2. `query_entity_ids()` 仍在扫 `graph.entities.values()`，需要实体名索引。
3. `LanceDBVectorStore.get()` 全表物化后线性找 key，逐 id 取回会二次放大。
