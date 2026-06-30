# 弱对象偏好过滤实验记录

日期：2026-06-30

本记录对应 `preference_object_filter` 小改动。它不是公开 benchmark headline，只作为本 PR 的可复现验收材料。

## 改动目的

上一轮 `explicit_preference_extraction` 提升了显式偏好召回，但 LongMemEval_S preference 切片显示一类
profile 噪声会进入长期记忆：

- `I love it`
- `I like these ideas`
- `I like those suggestions`
- `I love how they ...`
- `I enjoy use`

这类对象太弱，无法稳定回答未来偏好问题，却会污染 structured profile 和 preference evidence。目标是默认过滤
弱对象，同时保留具体对象，例如 `retro-style diners`、`aisle seats`、`red-eye flights`。

## 算法边界

新增默认开启配置：

- `Config.preference_object_filter`

过滤对象：

- 指代词或弱泛称：`it`、`this`、`that`、`these`、`those`、`things`、`stuff` 等。
- 弱建议/想法对象：`these ideas`、`those suggestions`、`these tips` 等。
- 上下文依赖的解释对象：`you mentioned ...`、`how they ...`、`how it ...` 等。

过滤同时应用于：

- 离线 `RuleExtractor` 的显式偏好句式。
- `ConsolidationEngine` 中来自任意 extractor 的显式偏好谓词，避免 LLM extractor 吐出 `likes it` 时绕过保护。

## 验收分层

没有跑完整 LongMemEval_S 500。理由：这是 profile precision guard，不是 retrieval/planner/answerer 改动；完整
500 + LLM judge 成本高且不能直接定位弱对象过滤是否生效。采用：

1. 单测：覆盖默认过滤、关闭开关、bench ablation 注册。
2. 离线消融：证明开启后保留具体偏好并移除 `loves it`，关闭后会保留弱对象。
3. 真实数据切片：LongMemEval_S `single-session-preference` 30 条，全量该类别，离线比较过滤前后弱对象数量。

## 命令与结果

### 单测 / 全量测试

```bash
python3 -m pytest
```

结果：`399 passed in 11.26s`

### 离线特征消融

```bash
python3 eval/ablate_features.py --jsonl results/preference_object_filter_ablation.jsonl
```

结果：`improved 18/18 features`

新增项：

- `preference_object_filter`: enabled 时保留 `retro-style diners`，且不包含 `loves it`；disabled 时会出现弱对象。

保存文件：

- `results/preference_object_filter_ablation.jsonl`

### LongMemEval_S preference 切片上下文验收

切片：`load_data("s")` 后筛选 `question_type == "single-session-preference"`，共 30 条。

运行方式：离线 `Memory(config=Config(preference_object_filter=...))`，不调用 answerer/judge；对比 enabled/disabled
的显式偏好事实数、弱对象事实数和 `PREFERENCE RECORDS` 覆盖。

保存文件：

- `results/preference_object_filter_lme_s_context30.jsonl`

最终摘要：

```json
{
  "items": 30,
  "items_with_removed_explicit_facts": 22,
  "enabled_explicit_facts": 252,
  "disabled_explicit_facts": 297,
  "enabled_weak_explicit_facts": 0,
  "disabled_weak_explicit_facts": 45,
  "enabled_context_records": 7,
  "disabled_context_records": 7
}
```

抽查移除对象 Top：

- `it`: 15
- `these ideas`: 5
- `those suggestions`: 4
- `those ideas`: 3
- `that`: 2
- `those tips`: 2

## 结论

本次改动通过单测、离线消融和真实 preference 类别切片验收。它把 LongMemEval_S preference 30 条中的弱对象显式
偏好事实从 45 降到 0，同时保持 `PREFERENCE RECORDS` 覆盖 7/30 不变。该结果证明 profile precision 有提升，
但不声称完整 LongMemEval_S accuracy 提升。
