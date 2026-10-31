# Engram 固定候选的剩余 410 题离线验证

日期：2026-10-30（北京时间）。

**主预算下的来源保留收益扩大到了未参与本轮开发诊断的样本，但完整验证仍有缺口，较小预算也发现了回退。候选继续关闭，不进入生产默认。** 410 题全部尝试，407 题缓存重放有效，3 题缺缓存。60,000 字符预算下，找齐标注来源的题数从 363/407 增至 387/407；没有观察到标注来源或短参考字面回退。这不是问答准确率提升，也不证明世界排名。

用户已选择暂不恢复在线评测。本轮不补在线模型请求、不重试凭据、不根据这批结果修改候选。

## 固定验证范围

候选是上一阶段的 `eval/context_replay_priority.py`：把被裁掉原文对应的已有摘要放到 summary 正文前部，不新增检索或抽取。源码 SHA-256 固定为 `722925cc112da646fc9e03a18f5a4cd694457135acf1fb9526d0e07ea34e01b5`。

LongMemEval_S 共 500 题，其中 30 道不可回答题不参加本次来源保留诊断。470 道可回答题分成开发 60 题和剩余 410 题，两组完全互补。名单、候选指纹、预算和门槛均在运行前固定；没有替换无效样本。这是同一个既有公开基准的后续验证，不能称为全新独立 benchmark。

| 题型 | 固定题数 | 有效 | 无效 |
|---|---:|---:|---:|
| knowledge-update | 62 | 62 | 0 |
| multi-session | 111 | 110 | 1 |
| single-session-assistant | 46 | 44 | 2 |
| single-session-preference | 20 | 20 | 0 |
| single-session-user | 54 | 54 | 0 |
| temporal-reasoning | 117 | 117 | 0 |
| 合计 | 410 | 407 | 3 |

每题只冻结一次装配输入，两臂共用同一份 blocks、证据需求、detail episodes 与原字符上限。60k 为主预算，32k/16k 为压力诊断。主预算必须存在来源恢复且无逐题来源/短字面回退，才值得继续 QA；缺缓存时不能把有效子集算成完整验证通过。

## 主预算结果

“完整标注来源”指完整原文记录或完整已有摘要在 context 中出现，不保证摘要包含所需答案；来源仅剩一部分也不等于不能回答。短参考字面诊断只检查规范化空白与大小写后的精确子串，260 题适用，不接受语义等价，不作为 QA 裁判。

| context | 完整标注来源（共 786） | 全部来源齐全（共 407 题） | 短参考字面出现（共 260） | 平均字符数 |
|---|---:|---:|---:|---:|
| full-context 原文参考，未运行回答 | 786 | 407 | 133 | 496,752.4 |
| 装配前冻结证据，未裁切 | 783 | 404 | 117 | 54,862.7 |
| 60k 基线 | 736 | 363 | 117 | 53,529.4 |
| 60k 候选 | 764 | 387 | 117 | 53,546.2 |

候选在 28 题中各增加一个完整标注来源，未失去原有标注来源；其中 24 题因此补齐全部来源。主预算只有 102/407 题实际发生裁切，其余样本不能用于证明裁切干预的收益。两组使用相同字符上限，实际平均长度略有差异，不能写成完全相同的 context 长度或 token 成本。

| 题型 | 基线：全部来源齐全 | 候选：全部来源齐全 |
|---|---:|---:|
| knowledge-update | 53/62 | 59/62 |
| multi-session | 92/110 | 100/110 |
| single-session-assistant | 44/44 | 44/44 |
| single-session-preference | 19/20 | 20/20 |
| single-session-user | 51/54 | 54/54 |
| temporal-reasoning | 104/117 | 110/117 |

这支持“前置回补可以改善来源表示的保留”，但主预算的短参考字面出现仍为 117/260，没有增加。不能用来源覆盖率代替回答正确率。

## 较小预算的回退

| 预算 | 完整标注来源：基线 → 候选 | 全来源题数：基线 → 候选 | 新增 / 丢失标注来源 | 有来源丢失的题数 | 短字面：基线 → 候选 |
|---:|---:|---:|---:|---:|---:|
| 32k | 248 → 524 / 786 | 56 → 219 / 407 | 289 / 13 | 13 | 103 → 111 / 260 |
| 16k | 27 → 208 / 786 | 13 → 109 / 407 | 188 / 7 | 7 | 76 → 89 / 260 |

所有来源丢失都发生在基线中完整保留、但候选不再完整保留的摘要；没有完整原文来源丢失。前置回补占用了已有摘要的预算，净增无法抵消逐题回退。虽然两档预算的短字面均未观察到丢失，这个窄诊断仍不能保证回答质量。因此不把候选推广到任意小预算，也不根据本批验证结果继续改排序或调阈值。

## 时延与计量边界

| 预算 | 基线 packing p50 / p95 ms | 候选 packing p50 / p95 ms | 平均字符数：基线 / 候选 |
|---:|---:|---:|---:|
| 60k | 0.012 / 0.134 | 0.032 / 0.398 | 53,529.4 / 53,546.2 |
| 32k | 0.088 / 0.170 | 0.285 / 0.466 | 31,848.6 / 31,848.5 |
| 16k | 0.075 / 0.142 | 0.212 / 0.367 | 15,997.1 / 15,997.1 |

时延仅计本机装配 helper 的三轮交替顺序暖运行，不含检索、embedding、抽取、回答或裁判。字符数不是 tokenizer tokens。本轮没有 QA accuracy、总模型 tokens 或端到端回答时延；full-context 也仅作同题原文参考，407 题均未触及其字符上限。

## 完整性与裁决

- 运行结束退出码为 0，日志有 410 个唯一 QID 和一个最终 summary。原 60 题未混入，没有换题。
- 总计 15,464 次缓存命中、3 次未命中。15,160 个唯一已用缓存文件均非空，内容 SHA 与回执一致。缺失题为 `1c549ce4`、`778164c6`、`65240037`，每题缺一个缓存文件；没有联网补齐或自动重试。
- 纯冻结重放与原运行的 411 条 JSON 记录**解析后逐条相同**，所有有效样本输入与 context 哈希相同。文件并非逐字节相同：仅 3 条 invalid 记录的 JSON 字段顺序不同，值和状态完全一致。不能把退出码 0 单独当成完整验收。
- 407 题未发现相同日期＋相同摘要文本对应多个 session ID 的身份碰撞。逐题来源丢失诊断只保存 ID、计数与哈希，不保存答案或原文。
- 新 runner 的 9 项测试和报告判定的 5 项测试通过；一轮独立只读核验确认了预注册、集合互补、源码固定及不补失败样本。相关原 core 在上一阶段已通过完整离线 pytest 和零密钥示例，本阶段未改算法源码。
- 预注册门槛输出 `inconclusive_incomplete_cache`：主预算的有效子集有收益且无观察到的回退，但 3 题缺失使完整验证仍不充分。小预算回退另行保留，不能用净增掩盖。

下一项能决定算法质量的证据是固定模型、固定裁判的真实 QA 与 full-context 对照，包含不可回答题、准确率、真实 token 用量和端到端时延。用户暂不恢复在线评测，本轮停在已完成的离线证据；不再通过换指标或继续调同一验证集替代这项缺失证据。

## 结果与复现

- [预注册](../results/context-holdout-20261030.preregistered.json)、[逐题日志](../results/context-holdout-20261030.jsonl)、[manifest](../results/context-holdout-20261030.jsonl.manifest.json)。
- [配对分析](../results/context-holdout-20261030.analysis.json)、[重放日志](../results/context-holdout-20261030-repack.jsonl)、[完整性与丢失诊断](../results/context-holdout-20261030.integrity.json)、[检查记录](../results/context-holdout-20261030.checks.json)。
- [开发 60 题实验](algorithm-context-evidence-20261030.zh-CN.md)与[第一阶段 500 题检索实验](algorithm-evidence-20261030.zh-CN.md)是不同层次的证据，不合并成 QA 总分。

在相同源码和已有本地数据、模型、抽取缓存下：

```bash
python3 -B eval/context_replay_holdout.py \
  --registration results/context-holdout-20261030.preregistered.json \
  --frozen-dir data/context-holdout-reproduction \
  --out results/context-holdout-reproduction.jsonl --device mps

python3 -B eval/context_replay_holdout.py \
  --registration results/context-holdout-20261030.preregistered.json \
  --frozen-dir data/context-holdout-reproduction \
  --out results/context-holdout-reproduction-repack.jsonl --replay-only

python3 -B eval/context_replay_report.py \
  --run results/context-holdout-reproduction.jsonl \
  --frozen-dir data/context-holdout-reproduction \
  --out results/context-holdout-reproduction.analysis.json
```

纯冻结重放不需要 dataset、embedding 或 LLM；首次冻结需要既有本地数据和缓存。缺少冻结输入或原抽取缓存的第三方目前无法独立重建全部输入，尚不能视为已公开可独立复现的发布结果。公开分数、论文数字及生产默认均未更新。
