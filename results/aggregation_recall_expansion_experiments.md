# aggregation_recall_expansion 实验记录

日期：2026-06-30

## 改动目标

上一轮 `numeric_aggregation_candidates` 已经能把金额、时长、页数整理成结构化候选表，但前提是相关 session 已经进入 lean context。真实样本 `7024f17c` 暴露了缺口：

- 问题：`How many hours of jogging and yoga did I do last week?`
- 旧行为：只召回了 yoga 的旧习惯，缺少 `30-minute jog` session，因此候选 INCLUDE 值为空
- 目标：对显式 `how many/how much/total/sum` 聚合问题，生成更高召回的 subqueries，例如 `workout`、`track workouts`、`jog`、`paid workshop`

## 实现边界

- 新开关：`Config.aggregation_recall_expansion`
- 只扩展显式聚合题面：`how many` / `how much` / `total` / `sum`
- 避免扩展 `What was the page count...` 这类题面，减少时间/题面约束被噪声 session 冲掉的风险
- 同时修正 `How much more miles per gallon...` 被误判成 money aggregation 的问题：MPG 不进入金额候选

## 验收命令

```bash
pytest tests/test_lean.py::test_numeric_aggregation_candidates_extract_money_and_hours tests/test_lean.py::test_evidence_planner_is_query_based_not_benchmark_based tests/test_lean.py::test_bench_preconsolidation_uses_aggregation_recall_expansion tests/test_eval_ablation.py -q
python3 eval/ablate_features.py --jsonl results/aggregation_recall_expansion_ablation.jsonl
pytest -q
```

## Offline 消融结果

- `results/aggregation_recall_expansion_ablation.jsonl`
- 结果：`improved 22/22 features`

## 真实数据验收

使用 LongMemEval_S 真实数值聚合问题做 context-level A/B，不调用 answerer/judge：

- `results/aggregation_recall_expansion_lme_s_context30.jsonl`
- 进入最终 numeric filter 的真实样本：27 条
- `7024f17c`：启用后候选 INCLUDE 值从 `[]` 变为 `[0.5]`，匹配答案 `0.5 hours`
- `gpt4_731e37d7`：候选求和保持 `720`，额外覆盖到一个 answer session
- `85fa3a3f`：候选数减少 1 个，原因是去掉了 `$250` 噪声值；这是去噪，不是召回损失
- `0ea62687`：MPG 问题已被排除在 money numeric candidates 外，避免 `$450,000` 这类金额噪声

注意：日志里的 `answer_session_hit_delta` 是通过在最终 context 文本里搜索 session id 得到的。结构化候选表本身不一定渲染 session id，因此它只能作为辅助信号；本次主要验收指标是候选值是否覆盖目标数值。

## 是否运行完整 500 QA

本次没有运行完整 LongMemEval_S 500 QA。原因：

- 改动目标是聚合证据召回与 context assembly，不是 answerer/judge 行为；
- 已保存真实 LongMemEval_S context-level A/B；
- 该改动只影响数值聚合子集，完整 500 应留给聚合召回 + 候选过滤再合并一轮后的阶段性验收。
