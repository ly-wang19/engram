# aggregation_constraint_filter 实验记录

日期：2026-06-30

## 改动目标

上一轮 `aggregation_recall_expansion` 解决了部分相关 session 召回不足的问题，但真实样本 `37f165cf` 暴露了另一个问题：候选表能抽到页数，但没有按题面时间约束过滤。

目标样本：

- `37f165cf`
- 问题：`What was the page count of the two novels I finished in January and March?`
- 旧候选：`[341, 416, 440]`
- 正确答案：`856 = 416 + 440`
- 噪声来源：`341 pages` 的局部上下文绑定了 `December`

## 实现边界

- 新开关：`Config.aggregation_constraint_filter`
- 当前只做低风险约束：当题面出现月份，且某个数值附近的小窗口绑定了不在题面里的月份，则该候选标记为 EXCLUDE
- 过滤作用在数值附近的局部窗口，而不是整句/整段，避免 `416-page ... before that ... December ... 341 pages` 这种句子里误伤 416

## 验收命令

```bash
pytest tests/test_lean.py::test_aggregation_constraint_filter_excludes_conflicting_month_values tests/test_eval_ablation.py -q
python3 eval/ablate_features.py --jsonl results/aggregation_constraint_filter_ablation.jsonl
pytest -q
```

## Offline 消融结果

- `results/aggregation_constraint_filter_ablation.jsonl`
- 结果：`improved 23/23 features`

## 真实数据验收

使用 LongMemEval_S 真实 numeric aggregation 切片做 context-level A/B，不调用 answerer/judge：

- `results/aggregation_constraint_filter_lme_s_context27.jsonl`
- 样本数：27

关键结果：

- `37f165cf`
  - 关闭过滤：include `[341, 416, 440]`，sum `1197`
  - 开启过滤：include `[416, 440]`，exclude `[341]`，sum `856`
  - 匹配答案 `856`
- `gpt4_731e37d7`：workshop 花费保持 `[20, 200, 500] = 720`
- `28dc39ac`：游戏时长保持 `[10, 25, 30, 5, 70] = 140`
- `7024f17c`：jog/yoga 保持 include `[0.5]`，旧习惯 `2.0` 仍为 EXCLUDE

## 是否运行完整 500 QA

本次没有运行完整 LongMemEval_S 500 QA。原因：

- 改动是 context 候选过滤，不直接改变 answerer/judge；
- 已使用真实 LongMemEval_S numeric aggregation 切片做 A/B，并保存 raw JSONL；
- 完整 500 QA 应留给聚合召回、候选过滤和答案策略合并后的阶段性验收。
