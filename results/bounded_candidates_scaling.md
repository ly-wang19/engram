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
