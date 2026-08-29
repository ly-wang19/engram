# 时序能力提升计划（2026-08-29，基于 LOCOMO 失败分析）

数据来源：`results/locomo200_main_deepseekjudge.jsonl` 的零成本失败分析。
LOCOMO temporal-reasoning：engram 78.4% vs full_context 94.6%（**−16.2**，37 题里错 8 题）。

## 8 道错题的完整分类

逐题看完后，**两种病因各占一半**，不能用同一招治：

### 甲类：跨事件时间跨度（4 题，全部弃答或算错）

| gold | 我们答的 | 病因 |
|---|---|---|
| four months | I don't know | 需 A 事件日期 − B 事件日期 |
| four months | I don't know | 同上 |
| 19 days | 9 days | 算错（但至少尝试了） |
| 2020 | I don't know | 需从相对表述回推年份 |

**关键证据**：追查 "How long did it take Joanna to finish writing her book?"（gold: four months）
——**"four months" 这个字符串不在 haystack 任何一句话里**。它是「开始写书」与「写完书」两个会话
日期相减的结果。

**根因**：`lean_context` 处处呈现绝对日期（`[2023-06-09]`、`valid from | valid until`），
但**从不呈现任意两个事件之间相隔多久**。answerer 拿到一串绝对日期，要自己做日期算术——它做不好，
于是弃答。全文基线反而因为能看到更多上下文线索而蒙对。

### 乙类：日期粒度被规约（4 题，全部答错且答得很自信）

| gold | 我们答的 | 偏移 |
|---|---|---|
| The week before 9 June 2023 | week preceding **July 6** | 月级 |
| first weekend of August 2023 | **2023-08-09** | 周级 |
| In 2021 | **2022**-04-20 | 年级 |
| Saturday after 27 January 2023 | I don't know | — |

**根因**：抽取把时间坍缩成单个时间戳（`valid_at`），原文的相对表述（"上周三"、"八月第一个周末"、
"那之后的周六"）在事实层不复存在。gold 恰恰要求的就是那种表述粒度。全文基线看得到原文，所以答对。

## 方案

### T1 · 事件间隔块（治甲类，~50 行）

`lean_context` 增加一个 `TIME SPANS` 块：对检索到的事实/会话，把两两之间的天数差算好给 answerer，
而不是让它自己减日期。

```
TIME SPANS (between retrieved events):
  2023-02-14 → 2023-06-09 = 115 days (~3.8 months)
  2023-06-09 → 2023-08-05 = 57 days (~1.9 months)
```

只在问题含 duration 意图（how long / how many days / since / between）时生成，避免污染其他题的上下文。
新增开关 `temporal_span_block`。**预期 +3~4 题**。

### T2 · 保留原文时间表述（治乙类，~30 行）

抽取时除了 `valid_at` 时间戳，额外把原文的时间短语存进 fact 的显示字段
（"the week before"、"first weekend of August"），并在上下文里与绝对日期并列呈现：

```
- [2023-06-09 · 原文: "the week before"] Caroline attended the support group
```

新增开关 `temporal_phrase_preservation`。**预期 +2~3 题**。风险低（只加信息不删）。

### 不做的事

- 不动检索层：8 道错题里检索都命中了（full_context 能答对说明信息在场，而我们的 3.4k 上下文
  也包含了对应会话）——**这是呈现问题，不是召回问题**。
- 不追总分：LOCOMO 总分 88.0 vs 89.0 的 1 分差距主要来自 single-hop（−3.6），
  而那类 90.4% 已接近该 benchmark 的噪声水平。

## 验收标准（先写死，避免事后找理由）

1. LOCOMO temporal 切片（37 题全量）：engram 从 78.4% 提升到 **≥86%**（+3 题以上）
2. 其余四类不倒退超过噪声带（±2 题）
3. LongMemEval temporal 切片（60 题）不倒退——**防止只治 LOCOMO 却伤了另一个 benchmark**
4. 全量 pytest 绿 + 新增针对性单测

**熔断**：T1+T2 一起做完一次验证；若标准 1 未达，不做第二轮调参，带数据汇报。
