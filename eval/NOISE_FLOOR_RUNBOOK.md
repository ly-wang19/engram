# 噪声底测量 · 运行手册

## 为什么先跑这个

`results/significance_headline.md` 已经量出：500 题、22% 分歧率下，最小可检测增益约 **2.94 点**。
但那是从**两个不同配置**的运行反推的分歧率。真正的噪声底——**同一份配置重跑会翻转多少题**——
项目里至今没有测量，只有"大约 6–10 题"这个观察。

这个数字是所有精度声称的地板：低于它的增益，无论重跑多少次都无法证明。**先钉死地板，再爬。**

已提交的三份 lean 日志（`..._v1` 78.8 / `..._v2_final` 83.6 / `headline_500` 79.0）都是**不同配置**，
所以不能用来算噪声底。必须新跑两次同配置。

## 需要你提供

| 项 | 说明 |
| --- | --- |
| `ARK_API_KEY`（+ `ARK_BASE_URL`） | 答题模型 doubao-seed-2.0-pro 与抽取模型 doubao-seed-1.6-flash |
| `DEEPSEEK_API_KEY` | 判分模型 deepseek-v3.2 |
| LongMemEval_S 数据集 | HuggingFace `xiaowu0162/longmemeval`，本仓库当前只有 2.7KB 的 sample |

嵌入用本地 `bge-small`，不需要 key。

## 成本（来自已提交日志的真实数据，非估算）

单次 `engram_lean` 500 题运行：送入答题模型的上下文合计 **4.78M tokens**，单题 p50 延迟 61s，
串行约 **8.4 小时**。跑两次即约 9.6M tokens / 17 小时（可并行以缩短墙钟时间）。

抽取与判分的调用量不在日志的 `tok` 字段里，属额外开销，按你的 provider 计费自行核算。

## 执行

两条命令**除 `--out` 外必须逐字符相同**——任何一个 flag 不同，测到的就不是噪声而是配置差异。

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 eval/bench.py \
    --data s --limit 500 --systems engram_lean \
    --answerer volcano:doubao-seed-2-0-pro-260215 \
    --judge volcano:deepseek-v3-2-251201 \
    --extractor volcano:doubao-seed-1-6-flash-250615 \
    --embedder bge-small --reasoning --persona \
    --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28 \
    --out results/noise_repeat_1.jsonl

# 同一条命令，只改 --out
#   --out results/noise_repeat_2.jsonl
```

## 读出结果

```bash
python3 eval/noise_floor.py results/noise_repeat_1.jsonl results/noise_repeat_2.jsonl \
    --system engram_lean
```

输出会给出：翻转题数与方向、两次运行的准确率差、以及据此推出的**最小可信增益**。

### 怎么解读

- **翻转本身不是 bug，也不是回归**，是测量仪器的误差棒。同配置下"翻成错"和"翻成对"是同一个现象，
  不要读成某一次更好。
- 如果工具报出 `WARNING: some pairs differ by more than chance`，说明两次运行**并非真的同配置**——
  检查 flag、数据集切片、模型版本是否一致，而不是把它当成噪声记下来。
- 拿到地板数字后，更新 `results/significance_headline.md` 与架构地图的「算法迭代的前置条件」，
  之后每个机制提案都要先回答「预期增益是否高于地板」。

## 已完成的免费前置检查

跑之前已确认本分支相对 main **检索行为无漂移**：离线评测两侧完全一致
（accuracy 100.0%/70.0%，context tokens 5.4/14.2），延迟因否定约束提前返回而更低。
所以测出的翻转可以归因于答题模型的不确定性，而不是本轮代码改动。
