# preference_reversal_extraction 实验记录

日期：2026-06-30

## 改动目标

补齐高置信偏好更新表达的抽取能力，例如：

- `I no longer like jazz.`
- `I stopped enjoying crowded lounges.`
- `I'm no longer into synthwave.`

这些表达应抽取为当前负向偏好，并复用已有 conflict resolver 的 preference reversal 逻辑，让旧的正向偏好非破坏性失效，形成 `supersedes` 链。

## 验收命令

```bash
pytest tests/test_smoke.py::test_preference_reversal_extraction_invalidates_old_like tests/test_smoke.py::test_preference_reversal_extraction_handles_stopped_liking tests/test_smoke.py::test_preference_reversal_extraction_can_be_disabled tests/test_eval_ablation.py -q
python3 eval/ablate_features.py --jsonl results/preference_reversal_extraction_ablation.jsonl
pytest -q
```

## 结果

- 针对性测试：通过。
- Offline feature ablation：`improved 20/20 features`。
- 全量测试：通过。

关键离线消融日志：

- `results/preference_reversal_extraction_ablation.jsonl`

## 真实数据扫描

本次改动的触发面非常窄。为了避免每个小补丁都浪费完整 500 QA，本次先做真实数据模式扫描：

```bash
python3 - <<'PY'
# 使用 eval.longmemeval.load_data('s') 扫描 LongMemEval_S 全 500 item
# 目标：no longer like/love/enjoy, stopped liking/loving/enjoying, no longer into/fond of/fan of
PY
```

扫描日志：

- `results/preference_reversal_lme_s_pattern_scan.jsonl`
- `results/preference_reversal_locomo_pattern_scan.jsonl`

扫描结论：

- LongMemEval_S：扫描 500 item，命中 0 条高置信偏好反转表达。
- LOCOMO：本地缺少 `eval/locomo10.json`，日志记录了缺失原因。

因此，本次没有运行完整 LongMemEval_S 500 QA。原因是目标触发模式在 LongMemEval_S 当前真实数据中命中 0 条，完整 QA 对该补丁无法提供有效增益信号；完整 500 应保留给会影响检索排序、上下文组装、planner 或公开 headline 数字的改动。
