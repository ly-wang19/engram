# numeric_aggregation_candidates 实验记录

日期：2026-06-30

## 改动目标

针对 LongMemEval_S 中 multi-session / temporal 聚合失败里的常见形态，给 read path 增加确定性的数值聚合候选：

- 金额：`$20`, `$200`, `$500`
- 时长：`70 hours`, `30-minute`
- 页数：`416-page`, `440 pages`

候选以 `AGGREGATION CANDIDATES` 表格进入 lean context，显式包含 `value` 和 `unit`，让 answerer 可以对 INCLUDE 行求和；明显过期的时长习惯（例如 `used to practice yoga`）会标为 EXCLUDE。

## 验收命令

```bash
pytest tests/test_lean.py::test_numeric_aggregation_candidates_extract_money_and_hours tests/test_lean.py::test_numeric_aggregation_candidates_can_be_disabled tests/test_eval_ablation.py -q
python3 eval/ablate_features.py --jsonl results/numeric_aggregation_candidates_ablation.jsonl
```

完整回归：

```bash
pytest -q
```

## Offline 消融结果

- `results/numeric_aggregation_candidates_ablation.jsonl`
- 结果：`improved 21/21 features`

## 真实数据验收

使用 LongMemEval_S 真实问题，不调用 answerer/judge，只构建 Engram lean context，对启用/禁用 numeric candidates 做 context-level A/B：

- `results/numeric_aggregation_lme_s_context30.jsonl`
- 选择：LongMemEval_S 中数值聚合相关的 30 个 multi-session / temporal-reasoning 问题
- 启用后出现候选表：26/30
- 禁用后出现候选表：0/30

关键真实样本：

- `gpt4_731e37d7`：workshop 花费候选为 `20 + 200 + 500 = 720`，匹配答案 `$720`
- `28dc39ac`：游戏时长候选为 `10 + 25 + 30 + 5 + 70 = 140`，匹配答案 `140 hours`
- `7024f17c`：旧习惯 `used to practice yoga ... 2 hours` 被标为 EXCLUDE；但 jog 证据没有被当前检索召回，说明下一步应优化聚合召回覆盖
- `37f165cf`：页数候选能抽取 `416` 和 `440`，但也保留了 `341` 这个历史页数干扰项；后续需要更强的时间/题面约束过滤

## 是否运行完整 500 QA

本次没有运行完整 LongMemEval_S 500 QA。原因：

- 改动是 context assembly 的结构化证据增强，不涉及 answerer/judge；
- 已用真实 LongMemEval_S 的数值聚合切片保存 context-level A/B；
- 完整 500 QA 成本高，且该补丁只影响 41 个左右的数值聚合问题，应留到聚合召回扩展或多项 aggregation 改动合并后做阶段性总验收。
