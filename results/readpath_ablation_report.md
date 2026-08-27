# 读路径特性消融报告（2026-08-27）

**指标**: answer-in-context（gold 答案是否进入检索上下文）——免 answerer/judge 的确定性必要条件指标。
**设置**: LongMemEval_S 前 60 题；extractor `volcano:doubao-seed-1-6-flash-250615`；embedder bge-small；
topk 15 / chunks 2 / extract-k 8；每特性 = 基线减该特性（边际贡献）。
**代码版本**: 修复前（`2a18eae` 之前的 main），刻意作为"修复前全景基线"。
**原始日志**: `results/readpath_ablation.jsonl`。

## 噪声带（先读这个）

基线重复两次：70.0% 与 78.3%，**噪声带 ±8.3pp**（extractor 非确定性所致）。
任何小于此带的 delta 都不构成证据。

## 三档结论（25 特性）

### hurts（关掉显著更好，超出噪声带）

| 特性 | 关掉后 | delta | 与噪声带比 |
|---|---|---|---|
| `provenance_chunk_promotion` | **90.0%** | **-15.8pp** | ~2× 带宽，全场最大单项 |
| `graph_path_reinforcement` | 83.3% | -9.2pp | 仅超带 1.1pp，边缘 |

`provenance_chunk_promotion` 与 5 题定向诊断互证（关它 1/5→4/5；其他嫌疑 ≤2/5）：其源会话
提升会挤占语义检索 chunk 名额，事实检索一偏就把携带答案的 session 挤出——single-session
弃答塌陷（50 题 A/B：user 57%→14%，assistant 100%→60%）的直接根因。
**处置**: 已修复为"语义保底"（promotion 最多占 chunk 预算一半，`2a18eae`），保留其
temporal/previous-value 收益而不再付 single-session 代价。
`graph_path_reinforcement` 仅边缘超带且单次测量，**不动**（避免调参循环）。二期复测。

### noise（±8.3pp 内，23 个特性全部落此档）

graph_proximity / graph_relation_awareness / graph_self_anchor / graph_entity_alias_anchor /
graph_negative_constraints / planner_location_chains / planner_project_chains /
planner_llm_decomposition / evidence_planner / evidence_budgeting / chain_evidence /
temporal_history_queries / provenance_evidence / explicit_preference_extraction /
preference_object_filter / preference_object_normalization / preference_reversal_extraction /
numeric_aggregation_candidates / aggregation_recall_expansion / aggregation_constraint_filter /
summary_fallback / procedural_memory / procedural_extraction

### helps（超噪声带的正贡献）

（无——本指标、本样本量下没有特性证明出超过噪声带的正边际贡献。）

## 读法与限度（重要）

1. **noise ≠ 没用**。本指标只测"答案是否在场"，不测"answerer 能否用好它"。chain_evidence
   对 knowledge-update 的价值（新旧值排序、失效标注）不体现为 gold 字符串在不在——那类价值
   要在 QA 层测。所以本报告**不构成关掉这 23 个特性的依据**，只说明它们在召回必要条件上
   未证明边际贡献。
2. **它构成的是"举证责任"基线**：后续任何读路径新特性，合并前至少要在本指标上超噪声带，
   或在 QA 层给出类别级证据——"方便切片上看起来更好"不再可接受（本仓库已有两次教训：
   entity_normalization 回滚、provenance promotion 挤占）。
3. `planner_llm_decomposition` 在本指标为 noise，但它给读路径加 LLM 调用（p50 延迟 79s 的
   贡献者）。**零证明收益 + 实测延迟成本** => 建议默认关闭候选，待 QA 层复核后决定。
