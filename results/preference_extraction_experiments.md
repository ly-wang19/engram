# 显式偏好提取实验记录

日期：2026-06-30

本记录对应 `explicit_preference_extraction` 小改动。它不是公开 benchmark headline，只作为本 PR
的可复现验收材料。

## 改动目的

离线 `RuleExtractor` 原先覆盖 `favorite_*`、`fan of`、`into/fond of`、过敏和饮食限制，但没有稳定
覆盖常见显式偏好句式，例如：

- `I prefer aisle seats`
- `I avoid red-eye flights`
- `I don't like crowded lounges`
- `I love/enjoy ...`

本次新增默认开启的 `Config.explicit_preference_extraction`，并在 eval harness 中提供
`engram_lean_no_explicit_preference_extraction` 作为 A/B 开关。

## 验收分层

没有为这次小规则改动跑完整 LongMemEval_S 500。理由：完整 500 + LLM judge 成本高、耗时长，且这次改动
主要影响事实提取覆盖，不是检索融合权重或 planner。采用更合适的分层验收：

1. 单测：覆盖提取、禁用开关、lean context preference block、bench ablation 注册。
2. 离线消融：零 API key，证明开启后能产生目标证据，关闭后不能。
3. 真实数据切片：LongMemEval_S 的 `single-session-preference` 30 条，全量该类别，离线检查提取事实和
   context evidence 差异。

## 命令与结果

### 单测 / 全量测试

```bash
python3 -m pytest
```

结果：`397 passed in 5.56s`

### 离线特征消融

```bash
python3 eval/ablate_features.py --jsonl results/preference_extraction_ablation.jsonl
```

结果：`improved 17/17 features`

新增项：

- `explicit_preference_extraction`: HIT when enabled, miss when disabled.

保存文件：

- `results/preference_extraction_ablation.jsonl`

### LongMemEval_S preference 切片上下文验收

切片：`load_data("s")` 后筛选 `question_type == "single-session-preference"`，共 30 条。

运行方式：离线 `Memory(config=Config(explicit_preference_extraction=...))`，不调用 answerer/judge；
对比 enabled/disabled 的显式偏好事实数、profile preference 数和 `PREFERENCE RECORDS` 是否出现。

保存文件：

- `results/preference_extraction_lme_s_context30.jsonl`

最终摘要：

```json
{
  "items": 30,
  "improved_items": 30,
  "enabled_explicit_facts": 297,
  "disabled_explicit_facts": 0,
  "enabled_context_records": 7,
  "disabled_context_records": 4
}
```

质量抽查：

- 初版规则曾过宽，会把 `Enjoy your journey` / `Avoid direct sunlight` 等助手建议误提取为偏好。
- 已收窄为：显式偏好需要 `I/we`；只有上一句本身是显式偏好句时，才允许 `and avoid...` 这类小写续句。
- 显式偏好事实默认挂到当前 `user_id`，避免 `I'm pretty sure...` 这类短语污染 self-name 后影响 subject。

### 失败/未采用的实验

尝试过极小 LLM 真实切片：

```bash
python3 eval/bench.py --data s --category single-session-preference --limit 2 \
  --systems engram_lean,engram_lean_no_explicit_preference_extraction \
  --answerer gemini-2.5-flash --judge gemini-2.5-flash --extractor gemini-2.5-flash \
  --workers 1 --persona --strategies --out results/preference_extraction_lme_s_pref2.jsonl
```

结果：LiteLLM/Gemini provider 连续报错并卡在提取线程中，已中断；未产出有效 JSONL，不作为质量结论。
失败原因记录在本文件中，未保留 0 字节输出文件。

## 结论

本次改动通过单测、离线消融和真实 preference 类别切片验收。它证明了新增显式偏好提取能增加 profile/context
中的可用证据，并且可通过开关做 A/B；没有声称完整 LongMemEval_S accuracy 提升。下一次若继续改检索权重、
planner 或公开结果数字，再跑更大样本或完整 500。
