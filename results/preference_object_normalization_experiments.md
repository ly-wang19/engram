# 偏好对象规范化实验记录

日期：2026-06-30

本记录对应 `preference_object_normalization` 小改动。它不是公开 benchmark headline，只作为本 PR 的可复现验收材料。

## 改动目的

`explicit_preference_extraction` 与 `preference_object_filter` 之后，LongMemEval_S preference 切片仍有一类
半具体对象进入 profile，例如：

- `sound of the avocado sauce`
- `idea of fruit kebabs`
- `idea of creating a shared Google Photos album`
- `your idea of grouping the movies by universe`

这些对象不是完全错误，但会让 structured profile 和 preference evidence 多一层无意义包装。目标是把它们规范化
为更可检索、更可复用的对象：

- `avocado sauce`
- `fruit kebabs`
- `creating a shared Google Photos album`
- `grouping the movies by universe`

## 算法边界

新增默认开启配置：

- `Config.preference_object_normalization`

当前规范化模式：

- `sound of X` -> `X`
- `idea of X` -> `X`
- `my/your/our/their idea of X` -> `X`
- `concept of X` -> `X`

规范化同时应用于：

- 离线 `RuleExtractor` 显式偏好句式。
- `ConsolidationEngine` 中来自任意 extractor 的显式偏好谓词，避免 LLM extractor 绕过 canonicalization。

## 验收分层

没有跑完整 LongMemEval_S 500。理由：这是 profile canonicalization，不是检索融合、planner 或 answerer 改动；
完整 500 + LLM judge 成本高，且不能直接定位对象规范化是否生效。采用：

1. 单测：覆盖默认规范化、关闭开关、bench ablation 注册。
2. 离线消融：证明开启后 facts 中只出现 canonical object，关闭后保留 `sound/idea of` 包装。
3. 真实数据切片：LongMemEval_S `single-session-preference` 30 条，全量该类别，离线比较 wrapped object 数量。

## 命令与结果

### 单测 / 全量测试

```bash
python3 -m pytest
```

结果：`401 passed in 5.68s`

### 离线特征消融

```bash
python3 eval/ablate_features.py --jsonl results/preference_object_normalization_ablation.jsonl
```

结果：`improved 19/19 features`

新增项：

- `preference_object_normalization`: enabled 时 facts 为 `avocado sauce` / `fruit kebabs`，disabled 时保留
  `sound of the avocado sauce` / `idea of fruit kebabs`。

保存文件：

- `results/preference_object_normalization_ablation.jsonl`

### LongMemEval_S preference 切片上下文验收

切片：`load_data("s")` 后筛选 `question_type == "single-session-preference"`，共 30 条。

运行方式：离线 `Memory(config=Config(preference_object_normalization=...))`，不调用 answerer/judge；对比
enabled/disabled 的 wrapped explicit preference 数量和 `PREFERENCE RECORDS` 覆盖。

保存文件：

- `results/preference_object_normalization_lme_s_context30.jsonl`

最终摘要：

```json
{
  "items": 30,
  "items_with_removed_wrappers": 30,
  "enabled_explicit_facts": 253,
  "disabled_explicit_facts": 252,
  "enabled_wrapped_explicit_facts": 0,
  "disabled_wrapped_explicit_facts": 114,
  "enabled_context_records": 7,
  "disabled_context_records": 7
}
```

抽查规范化 Top：

- `sound of the cascara latte` -> `cascara latte`
- `idea of fruit kebabs` -> `fruit kebabs`
- `sound of the avocado sauce` -> `avocado sauce`
- `idea of creating a shared Google Photos album` -> `creating a shared Google Photos album`
- `your idea of grouping the movies by universe` -> `grouping the movies by universe`

## 结论

本次改动通过单测、离线消融和真实 preference 类别切片验收。它把 LongMemEval_S preference 30 条中的 wrapped
显式偏好对象从 114 降到 0，同时保持 `PREFERENCE RECORDS` 覆盖 7/30 不变。该结果证明 profile canonicalization
更干净，但不声称完整 LongMemEval_S accuracy 提升。
