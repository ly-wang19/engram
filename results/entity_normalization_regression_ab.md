# entity_normalization 回归 A/B（2026-08-27）

结论：commit `185e0ac`（图谱实体守门 + 规范名折叠）在 LongMemEval_S 上造成严重回归，已由 `60682c5` 回滚。

## 设置

同一 rig、同一 4 题、同一 answerer/judge/extractor/embedder，唯一变量是那 5 个文件的版本。

- data: longmemeval_s (前 4 题)
- answerer: `volcano:doubao-seed-2-0-pro-260215`
- judge: `deepseek`（官方 API；**偏离项**：headline 用的 `volcano:deepseek-v3-2-251201` 端点已下线 404）
- extractor: `volcano:doubao-seed-1-6-flash-250615`
- embedder: `bge-small`
- 参数: `--reasoning --persona --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28 --workers 2`

## 结果

| 类别 | 带 entity_normalization | 回滚后 |
| --- | --- | --- |
| knowledge-update | 0% (1) | 100% (1) |
| multi-session | 0% (1) | 100% (1) |
| single-session-user | 0% (1) | 0% (1) |
| temporal-reasoning | 100% (1) | 100% (1) |
| **OVERALL** | **25%** | **75%** |
| avg context tokens | 7686 | 7592 |

同批 full_context 基线为 75%（4 题）。

原始日志：`data/smoke4.jsonl`（带改动，含 full_context）、`data/ab_before.jsonl`（回滚后）。

## 根因

`entity_worthy()` 阈值（>40 字符、>8 词、含从句标点含 ASCII `:` `?` `!` `;`）按中文语料标定。
英文实体值抽样 15 个，误杀 4 个（27%）：

- `a specific episode COVID-19 vaccine rollout on February 10th`
- `$12 cashback for a $10 Amazon gift card`
- `wireless blood pressure monitor from Omron`
- `learning_about_sea_turtle_conservation_organizations`

叠加 `graph_builder.add_fact()` 的实现：主语或宾语任一不合格即 `return`，**整条边**不入图，
相关事实完全失去 graph proximity 与 n-hop 可达性。

## 若要重做

1. 阈值必须按语言分别标定（或改用与语言无关的信号：是否含限定动词/是否为完整从句）。
2. 守门只应作用于**该端点**，不应丢弃整条边；宁可让一端入图。
3. 合并前必须有 LongMemEval 切片 A/B，不能只有单测 + 个人语料观感。
4. `canon_entity_name()`（大小写/分隔符折叠）本身未被单独证伪，可与守门解耦后单独验证再考虑。
