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

---

## 结果：T1+T2 已回滚（2026-08-29）

### 数据

同 200 题、同 rig、0 坏行：

| 类别 | 改前 | 未门控 | 门控后 |
|---|---:|---:|---:|
| multi-hop | 22/28 | 23 | **24** (+2) |
| temporal-reasoning | 29/37 | 31 | 30 (+1) |
| open-domain | 5/7 | 5 | 5 |
| adversarial | 45/45 | 43 | 43 (−2) |
| single-hop | 75/83 | 71 | **70 (−5)** |
| **总计** | **176** | 173 | **172 (−4)** |

LOCOMO temporal 单类曾测得 89.2%（+10.8），但全类回归后只剩 +1 —— **单类切片的漂亮数字没有兑现**。
LongMemEval temporal 护栏 83.3%（基线 83.5%，噪声内），没伤到另一个 benchmark，但总体为负。

### 为什么没提升：三次归因全错，真因是测量噪声

- **第 1 次归因**：`said:` 后缀污染非时序题 → 加门控。**被证伪**：门控后 single-hop 该回到 0
  （其渲染路径与改前逐字相同），实际仍是 −5。
- **第 2 次归因**：改怪 T1 的 `TIME SPANS` 误触发。**也站不住**。
- **真因**（决定性检验）：对比两次运行中 single-hop 的上下文 token 数——
  **83 题里 tok 完全相同的有 0 题**，两次都答对的 68 题平均变动 481 tokens，
  极端差到 −2597 / +3194。

  上下文本应逐字相同（代码路径未变），却普遍剧烈变动 → **抽取阶段的 LLM 非确定性主导了差异**。
  每次跑分，`doubao-seed-1-6-flash` 从同样的会话里抽出的事实集就不同，检索到的证据随之不同。
  所谓「−5 回归」和「+2 改善」**都在这个噪声之内**，不是代码行为的差异。

### 教训（比修复本身更重要）

1. **这套 rig 的 A/B 不可靠**：我们一直用「同题对比」当因果证据，但前提「上下文不变则代码变化可归因」
   在 LLM 抽取器下不成立。以后做读路径 A/B，**必须先固定抽取结果**（缓存事实集或用规则抽取器），
   否则测的是抽取器的随机性。
2. **单类切片会骗人**：temporal 单类 +10.8 → 全类 +1。小样本 + 高方差 = 假信号。
3. 三次归因失败的共同点：我每次都在**没有先验证"变化是否真实"**的情况下直接解释变化的成因。
