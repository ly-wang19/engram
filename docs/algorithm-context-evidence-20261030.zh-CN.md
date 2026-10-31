# Engram 上下文证据保留：缓存冻结离线实验

日期：2026-10-30（北京时间）。

后续固定候选验证已完成：410 题尝试、407 有效、3 缺缓存，主预算的来源收益扩大到剩余样本，但小预算发现回退，完整验证仍不充分。最终状态参见 [410 题验证报告](algorithm-context-holdout-20261030.zh-CN.md)。以下保留开发阶段原始结果，不能作为后续验收通过的声明。

**最后候选保留待 QA 验证，默认不启用。** 第一版将回补摘要追加到末尾，没有改善；真实损失诊断发现回补行会再次被裁掉。第二版在同一份冻结输入中前置回补摘要，主预算下完整标注来源从 98/100 增至 99/100、全部来源覆盖从 58/60 题增至 59/60 题，未观察到标注来源或短字面退步。收益足以保留实验原型，但没有证明问答准确率或世界排名提升。

## 实验与结果

从 LongMemEval_S 六个题型各取 10 道可回答且有标注来源的题目，按 `SHA256("context-packing-v1:" + qid)` 排序选取，不依据答案或旧错误挑题。60 题全部有效，2,259 次模型请求均命中已有缓存，0 缺失；没有在线 LLM 请求或缓存补全。

每题只构建一次 memory，截获最终裁切前的 blocks、证据需求和 detail episodes。两组共用这份冻结输入，仅改变 `packing_summary_fallback`。预算和晋级门槛在查看真实配对结果前写入 `HANDOFF.md`：60,000 字符为主预算，32,000/16,000 为次级压力诊断；主预算必须恢复真实来源且无来源或字面诊断回退，才晋级后续 QA。无效题不补选。

“完整来源”指一份完整原文记录或一条完整已有摘要仍在 context 中，**不代表摘要含有所需答案**。反过来，未完整保留来源也不代表剩余片段不能回答。短参考诊断只对 33 道长度不超过 120 字符且包含字母的参考答案，检查规范化空白及大小写后的精确子串，不接受语义等价，不作为 QA 裁判。

| context | 完整标注来源数（共 100） | 找齐全部来源的题数（共 60） | 短参考字面出现（共 33） | 平均字符数 |
|---|---:|---:|---:|---:|
| full-context 原文参考，未运行回答 | 100 | 60 | 21 | 496,731.1 |
| 装配前冻结证据，未裁切 | 100 | 60 | 20 | 53,150.8 |
| 60k 基线 / 候选 | 98 / 98 | 58 / 58 | 20 / 20 | 52,429.8 / 52,429.8 |
| 32k 基线 / 候选 | 37 / 37 | 15 / 15 | 17 / 17 | 31,736.2 / 31,736.2 |
| 16k 基线 / 候选 | 3 / 3 | 1 / 1 | 9 / 9 | 15,999.9 / 15,999.9 |

上表的候选为第一版“末尾回补”。主预算只有 7/60 题发生裁切，第一版只改变 1 题的输出字节，未恢复任何标注来源。两个次级预算也均未增加标注来源；比装配前少出现的短参考分别为 3 和 11 个，第一版均未恢复。它未通过晋级门槛。

第二版根据下文真实损失定位，将同样的已有回补摘要放到 summary 正文前部，复用原预算裁切；不读问题答案或 gold 决策，不改检索和抽取。第二候选运行前再次记录范围，仍使用原 60 题及三档预算，未补题，未增加模型调用。

| 预算 | 完整标注来源：基线 → 前置回补 | 全部来源题数：基线 → 前置回补 | 短参考字面：基线 → 前置回补 | 平均字符数：基线 / 前置回补 |
|---:|---:|---:|---:|---:|
| 60k（主） | 98 → 99 / 100 | 58 → 59 / 60 | 20 → 20 / 33 | 52,429.8 / 52,429.8 |
| 32k（次级） | 37 → 74 / 100 | 15 → 44 / 60 | 17 → 18 / 33 | 31,736.2 / 31,736.2 |
| 16k（次级） | 3 → 40 / 100 | 1 → 29 / 60 | 9 → 11 / 33 | 15,999.9 / 15,999.9 |

主预算救回 `6a27ffc2` 的一个来源，其他题无标注来源增减。三档预算均未观察到标注来源或字面回退；次级预算分别在 36、30 题增加来源。前置回补会挤占其他摘要：32k 下完整摘要总数 597→588，16k 下 137→135，即使这些被挤出的摘要不在标注来源集合内，也不能断言对真实回答完全无害。第二版是依据这批样本诊断后的开发集实验，不能当作独立留出集的确认；不继续在这 60 题上加候选或调参。

字符数不是 tokenizer tokens，更不是总模型成本。本次没有调用回答模型或裁判，因此没有 QA accuracy、总 tokens 或端到端回答时延。full-context 行仅为同题原文参考，60 题均没有触及 full-context 字符上限。

最后候选的装配 helper 本机三轮交替顺序诊断时延如下；仅计字符串装配，不含建库、检索、embedding、模型调用和结果分析，不作为生产性能承诺。

| 字符预算 | 基线 p50 / p95 ms | 候选 p50 / p95 ms |
|---:|---:|---:|
| 60,000 | 0.011 / 0.093 | 0.021 / 0.228 |
| 32,000 | 0.074 / 0.176 | 0.240 / 0.666 |
| 16,000 | 0.063 / 0.161 | 0.181 / 0.500 |

## 实现与可复现边界

### 真实损失定位

[脱敏逐题诊断](../results/context-replay-20261030.losses.json)将两种 packing 损失与一种上游压缩损失分开：

| QID | 位置 | 可验证现象 |
|---|---|---|
| `6a27ffc2` | 原文裁切，回补再次裁切 | 第二条标注原文只剩前 10,522/19,125 字符；已有 681 字符摘要被追加到摘要配额外，因此第一候选并未真正显示它。 |
| `6456829e` | 原有摘要块裁切 | 第二条标注来源仅进入 summary，没有选为 detail；摘要位于块正文 22,708 字符后，而最终只保留前 18,228 字符。 |
| `5a4f22c0` | 装配前的摘要压缩 | 完整原文中存在短参考字面，但已选摘要遗漏该人物关系；字面在 packing 之前就已缺失，调整预算不能自动补回。 |

这些是表示层的证据，仍未证明回答模型会答错。尤其第一题的参考答案不属于短字面诊断范围，不能用其他题的字面稳定替代该题的语义验收。

### 工程边界

- `Memory._pack_lean_blocks` 统一旧预算出口。实验开关默认 `False`；开启时只复用被裁掉原文的已有摘要，不增加检索、抽取、模型调用或写入。
- core 中的开关仍是第一版“末尾回补”，不应启用。通过本轮来源门槛的第二版仅在 `eval/context_replay_priority.py`，尚未接入生产读路径。它的 manifest 记录父运行身份、候选源码指纹，并逐题确认三档 baseline 与原运行字节一致。
- `eval/context_replay.py` 的缓存读取不构造在线 provider，不写模型缓存；任何缺失、空值或不可读回复都使整题无效，即使生产 fallback 吞掉异常也不例外。
- manifest 记录数据、模型、选题、运行参数、八份相关源文件指纹；每题绑定 manifest，并记录所用缓存 key / reply hash。源漂移、错题身份和损坏冻结文件不能混入有效分母。
- 纯冻结重放不加载 dataset、embedding 或 LLM。第一版和第二版各自的正式运行与重放结果 JSONL 均**逐字节相同**。完整输入保存在忽略的 `data/` 目录，结果文件只记录计数、来源 ID、哈希和诊断值。
- 本实验是既有开发基准上的分层 60 题，不是完整 500 题 QA，也不是独立留出集。缓存生成的历史模型版本仍只能按缓存标签识别。
- 零缓存缺失只证明所请求文本可重放，不证明所有抽取语义有效或所有内部 fallback 均未发生。

## 证据与复现

- [逐题日志](../results/context-replay-20261030.jsonl)、[manifest](../results/context-replay-20261030.jsonl.manifest.json)、[配对分析](../results/context-replay-20261030.analysis.json)、[纯重放](../results/context-replay-20261030-repack.jsonl)。
- 最后候选：[逐题日志](../results/context-replay-20261030-priority.jsonl)、[manifest](../results/context-replay-20261030-priority.jsonl.manifest.json)、[配对分析](../results/context-replay-20261030-priority.analysis.json)、[重放](../results/context-replay-20261030-priority-repack.jsonl)。
- [验证记录](../results/algorithm-context-checks-20261030.json)。完整离线 pytest、零密钥 quickstart 和相关 diff 检查通过。
- [第一阶段的 500 题检索实验](algorithm-evidence-20261030.zh-CN.md) 是另一项证据：k=4 小幅增益伴随检索耗时增加，不能与本轮不同分母、不同层次的诊断合成一个总分。

在同一源版本、已有公开数据、BGE-small 模型及指定抽取缓存的环境中：

```bash
python3 -B eval/context_replay.py --per-category 10 --device mps \
  --frozen-dir data/context-replay-reproduction \
  --out results/context-replay-reproduction.jsonl

python3 -B eval/context_replay.py --replay-only \
  --frozen-dir data/context-replay-reproduction \
  --out results/context-replay-reproduction-repack.jsonl

python3 -B eval/context_replay_report.py \
  --run results/context-replay-reproduction.jsonl \
  --frozen-dir data/context-replay-reproduction \
  --out results/context-replay-reproduction.analysis.json

python3 -B eval/context_replay_priority.py \
  --frozen-dir data/context-replay-reproduction \
  --run results/context-replay-reproduction.jsonl \
  --out results/context-replay-priority-reproduction.jsonl

python3 -B eval/context_replay_report.py \
  --run results/context-replay-priority-reproduction.jsonl \
  --frozen-dir data/context-replay-reproduction \
  --out results/context-replay-priority-reproduction.analysis.json
```

缓存缺失时明确产出 invalid，不自动联网补齐。冻结后的纯装配可以无模型复现；没有冻结文件或原抽取缓存的第三方目前无法重建全部输入，因此尚不满足公开可独立复现的发布条件。本轮不修改对外榜单、论文数字或默认算法配置。
