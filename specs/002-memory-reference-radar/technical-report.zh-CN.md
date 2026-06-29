# Engram 记忆框架技术报告：面向可验证算法领先的架构路线

**日期**：2026-06-29

**关联 Spec-Kit feature**：`002-memory-reference-radar`

**适用范围**：内部技术路线、架构设计、后续算法实现规划。本文不是公开营销稿；任何公开性能主张仍必须来自 Engram 自己的 `eval/` harness 与已提交的 `results/*.jsonl` 原始日志。

## 1. 执行摘要

Engram 的目标不是做一个普通的「记忆插件」，而是做一个可自托管、可复现、可审计、可扩展的 LLM agent 长期记忆基础设施。真正的领先不应靠一句「世界第一」来证明，而应靠三件事同时成立：

1. **准确率领先**：在中立、可复现的 benchmark 上，检索出的精确上下文比 full-context baseline 更准。
2. **成本与延迟领先**：在准确率不牺牲的前提下，显著降低 token 和 p50/p95 latency。
3. **证据领先**：每一个公开数字都有命令、配置、原始日志和 full-context baseline 可复跑。

我们当前的战略判断是：没有单一模式能赢。要把公开领域最强的做法组合成 Engram 自己的内核：

- 原始证据保真：Episode/raw chunks 永远是事实来源。
- 原子事实与双时间轴：Fact 有 `valid_at`/`invalid_at` 和 `created_at`/`expired_at`。
- 非破坏性更新：矛盾事实通过 `supersedes` 和 invalidation 保留演化链。
- 混合读取路径：raw chunks + facts + graph paths + summaries，而不是 facts-only。
- 多跳图检索：实体识别、n-hop/PPR-style expansion、时间过滤、RRF 融合。
- 睡眠期 consolidation：摘要、画像、心智模型、意图/程序记忆异步构建。
- 可复现 harness：任何算法改进必须报告 accuracy + tokens + latency。

一句话：**Engram 要成为「双时间轴图谱记忆内核 + 混合证据检索 + 多跳规划器 + 可复现评分台」**。

## 2. 设计原则

### 2.1 学习公开思想，做 clean-room 实现

我们可以学习公开仓库、论文和产品页面的架构模式，但不能直接复制代码、prompt、schema 或 benchmark artifact，除非后续明确完成 license review 并确认兼容。对 GPLv3 等 copyleft 项目，默认只做 architecture-only clean-room 借鉴。

### 2.2 内部可以野心很大，公开必须证据很硬

内部目标可以是打造全球领先的记忆框架；公开表达必须克制：

- 不写未复现的竞品对比。
- 不写没有 raw logs 的「SOTA」「#1」「世界第一」。
- 不把第三方 benchmark 数字当成 Engram 证据。
- 不牺牲 AGPL + commercial 双许可的清晰边界。

### 2.3 任何能力都必须能被 ablation

每个候选能力进入实现前，都要回答：

- 改的是写路径、consolidation、graph store、read path、profile memory、procedural memory、runtime profile，还是 eval harness？
- 预期提升哪个 benchmark 类别？
- accuracy、tokens、p50/p95 latency 方向是什么？
- 如果收益在方差内、prompt 膨胀、延迟过高或 provenance 不清，如何回滚？

## 3. 外部参考系统分层

### 3.1 P0：直接记忆系统与强竞品

| 系统 | 核心启发 | Engram 吸收方向 |
| --- | --- | --- |
| [Hy-Memory](https://hy-memory.com/) / [Tencent Hunyuan Memory](https://memory.hunyuan.tencent.com/) | 六层记忆、System-1/System-2、evolution chain | chain-aware retrieval、session/profile/mental/intent 派生层、runtime profiles |
| [Mem0](https://github.com/mem0ai/mem0) | 通用 memory layer、SDK/API、用户/agent/session 记忆 | 服务 API、实体链接、OpenAI/MCP 兼容面 |
| [Graphiti](https://github.com/getzep/graphiti) / Zep | temporal knowledge graph、实时更新 | 双时间轴图谱读取、时间路径展开、图写入契约 |
| [Letta](https://github.com/letta-ai/letta) / MemGPT | core/recall/archival memory、agent 可编辑状态 | 过程记忆控制、可审计 memory-edit 操作 |
| [LangMem](https://github.com/langchain-ai/langmem) | semantic/episodic/procedural memory primitives | typed memory API 命名与 agent graph 集成 |
| [Cognee](https://github.com/topoteretes/cognee) | KG memory、ingestion pipeline、自托管部署 | 图谱 ingestion 形态、图后端评估 |
| [Supermemory](https://github.com/supermemoryai/supermemory) | context engine、跨应用记忆 | context assembly API、本地优先服务包装 |
| [Hindsight](https://github.com/vectorize-io/hindsight) | agent memory that learns | failure-derived memory、反馈驱动行为改进 |
| [MemPalace](https://github.com/MemPalace/mempalace) | raw recall 和 benchmarked memory | raw-verbatim evidence 保留、chunk provenance scoring |

### 3.2 P1：算法与架构原语

| 系统 | 核心启发 | Engram 吸收方向 |
| --- | --- | --- |
| [SimpleMem](https://github.com/aiming-lab/SimpleMem) | lifelong memory compression、自演化记忆 | sleep-time semantic compression、summary/fact/chunk fusion ablation |
| [A-Mem](https://github.com/agiresearch/a-mem) | 动态链接记忆、Zettelkasten 式组织 | memory-link evolution、associative expansion |
| [HippoRAG](https://github.com/OSU-NLP-Group/HippoRAG) | KG + Personalized PageRank | graph proximity retriever、PPR-style candidate expansion |
| [LightRAG](https://github.com/HKUDS/LightRAG) | 轻量 graph + vector 检索 | lightweight graph/BM25/vector fusion baseline |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | community summary、local/global query | community summaries、global/local/hybrid graph query |
| [RAPTOR](https://github.com/parthsarthi03/raptor) | recursive abstraction tree | summary tree、query-routed abstraction levels |
| [LLM Wiki](https://github.com/nashsu/llm_wiki/blob/main/README_CN.md) | source -> wiki -> schema/purpose、四信号图谱、Louvain、知识空白 | human-readable memory workspace、purpose-aware consolidation、graph diagnostics |
| [Generative Agents](https://github.com/joonspk-research/generative_agents) | memory stream、reflection、planning | salience/relevance/recency calibration、reflection queue |
| [Reflexion](https://github.com/noahshinn/reflexion) | 失败反馈转 verbal lessons | failure-to-procedure memory |
| [ExpeL](https://github.com/LeapLabTHU/ExpeL) | 从历史任务抽经验 | experience extraction、skill/lesson retrieval |
| [MemoryBank](https://github.com/zhongwanjun/MemoryBank-SiliconFriend) | 长期对话用户适配 | profile memory update and decay baselines |

### 3.3 P2：产品面与部署形态

| 系统 | 核心启发 | Engram 吸收方向 |
| --- | --- | --- |
| [MemOS](https://github.com/MemTensor/MemOS) | memory OS、memory cubes、反馈修正 | namespace/cube model、feedback correction UX |
| [MemoryOS](https://github.com/BAI-LAB/MemoryOS) | 短/中/长期 personalized memory | profile/episodic/procedural lifecycle policy |
| [memU](https://github.com/NevaMind-AI/memU) | personal memory、skill evolution | personal profile、skill memory surface |
| [Memobase](https://github.com/memodb-io/memobase) | user profile memory | profile/identity schema、更新策略 |
| [agentmemory](https://github.com/rohitg00/agentmemory) | MCP-first coding-agent memory | MCP server ergonomics、coding-agent workflows |

## 4. Engram 目标架构

### 4.1 写路径：System-1 快写，System-2 慢思考

System-1 的目标是低延迟、无 LLM 阻塞：

- 追加 lossless Episode。
- 做轻量身份/实体解析。
- 生成基础 embedding 或 hash fallback。
- 入队异步 consolidation。

System-2 的目标是高质量、可审计：

- 从 Episode 抽取原子 Fact。
- 构建双时间轴知识图谱。
- 做 cheap-first conflict detection。
- 生成 session summary、profile summary、mental model、intent/procedure records。
- 更新 salience、decay、reinforcement。

### 4.2 读路径：不是 top-k，而是 evidence assembly

目标读路径：

1. Query understanding：识别实体、时间、意图、是否多跳。
2. Multi-hop planning：拆成子查询或图扩展种子。
3. Parallel retrieval：
   - dense vector
   - BM25/lexical
   - graph proximity
   - raw session chunks
   - fact/profile/procedural memory
   - derived summaries
4. Temporal filtering：按 valid time 与 transaction time 做 as-of 过滤。
5. Fusion：RRF + recency/salience/graph proximity。
6. Chain expansion：对命中的 fact/profile 展开 bounded `supersedes` 链。
7. Abstention gate：记忆中没有就说没有。
8. Context assembly：去重、provenance-tagged、token-budgeted。

### 4.3 关键数据不变量

- Episode 是不可丢失的原始证据。
- Fact 是 atomic claim，不是摘要段落。
- Contradiction 永远不 hard-delete，必须 invalidation。
- Derived memory 不能变成无来源真相，必须回指 Episode/Fact。
- Public benchmark claim 必须有 raw log。

## 5. 七个优先算法候选

### 5.1 Chain-aware retrieval

**来源启发**：Hy-Memory evolution chain、Graphiti temporal updates、Engram 现有 `supersedes` 模型。

**Engram 形态**：当 read path 命中一个 fact/profile record 时，在 token budget 内展开其 current fact、superseded fact、superseding fact、valid/invalid time 和 provenance。

**预期收益**：提升知识更新、偏好变化、时间问答。

**风险**：链太长导致 prompt 噪音；旧事实误混入当前事实；as-of 语义不清。

**评测门槛**：LongMemEval knowledge-update/temporal slice + synthetic update set；报告 accuracy/tokens/p50/p95。

### 5.2 Raw evidence fusion hardening

**来源启发**：MemPalace、Supermemory、Mem0，以及 Engram M1 的 load-bearing finding。

**Engram 形态**：把 raw chunks、facts、graph paths、derived summaries 明确建模为不同 evidence classes，context assembly 时按证据类型、provenance、token budget 做融合。

**预期收益**：降低 facts-only 的细节遗漏，提升答案可引用性。

**风险**：raw chunk 过多带来 full-context 噪音；重复证据浪费 token。

**评测门槛**：LongMemEval_S full set ablation：facts-only vs raw-only vs hybrid vs hybrid+graph。

### 5.3 Derived memory layers

**来源启发**：Hy-Memory、RAPTOR、GraphRAG、SimpleMem、LLM Wiki。

**Engram 形态**：

- SessionSummary：会话摘要。
- ProfileSummary：用户画像摘要。
- MentalModel：长期偏好、推断模型、稳定倾向。
- Intent/Procedure：目标、工作流、操作经验。
- Workspace page：可选的人类可读视图。

**预期收益**：长历史压缩、低 token recall、用户长期一致性。

**风险**：摘要幻觉、过度抽象、来源断裂。

**评测门槛**：每个 derived artifact 必须能回指 source episodes/facts；benchmark 中单独做 with/without derived layer ablation。

### 5.4 Graph proximity retriever

**来源启发**：HippoRAG、LightRAG、GraphRAG、LLM Wiki 四信号图谱。

**Engram 形态**：query entity 作为种子，进行 bounded n-hop/PPR-style expansion，并叠加：

- graph distance
- edge confidence
- temporal validity
- source overlap
- recency/salience

**预期收益**：提升 multi-hop、multi-session、entity-chain 问题。

**风险**：图扩展过宽导致 distractors；图构建质量差时放大错误。

**评测门槛**：multi-hop 类别 accuracy 提升，tokens 不接近 full-context，p95 latency 保持 read-path 目标。

### 5.5 Knowledge workspace diagnostics

**来源启发**：LLM Wiki、GraphRAG、Cognee。

**Engram 形态**：可选生成 human-readable memory workspace，用于维护者查看：

- source-backed pages
- purpose/context
- sparse areas
- bridge nodes
- missing-link suggestions
- community summaries

**预期收益**：提升记忆可维护性、可审计性和 consolidation 质量。

**风险**：workspace pages 被误当成 source-of-truth；生成内容污染事实层。

**评测门槛**：workspace 只能是 derived view，必须回指 source；不得改变默认 read path，除非后续 ablation 证明有益。

### 5.6 Reflection and experience memory

**来源启发**：Generative Agents、Reflexion、ExpeL、A-Mem、Hindsight。

**Engram 形态**：失败、重复任务、用户修正、高 salience episode 进入 procedural memory 候选，经验证后形成 lesson/skill memory。

**预期收益**：提升 agent 重复任务表现、减少同类错误。

**风险**：把一次性失败过度泛化成永久规则；把用户修正和事实混淆。

**评测门槛**：agent workflow replay，比较有无 procedural lesson 的任务成功率、token、延迟。

### 5.7 Runtime profiles

**来源启发**：Hy-Memory、MemOS、Mem0、Supermemory。

**Engram 形态**：

- `lite`：raw chunks + lexical/vector。
- `standard`：facts + raw chunks + conflict handling。
- `graph`：加入 bi-temporal graph + multi-hop expansion。
- `consolidated`：加入 summaries/mental/procedural derived layers。

**预期收益**：用户可选择延迟/成本/准确率平衡；harness 可公平 ablation。

**风险**：profile 太多导致配置复杂；默认模式不清。

**评测门槛**：每个 profile 都有同一 benchmark 上的 accuracy/tokens/latency 三联表。

## 6. 推荐实施顺序

### 阶段 A：先打穿 evidence-first read path

1. Chain-aware retrieval。
2. Raw evidence fusion hardening。
3. Context assembly 证据类型化。

原因：这是最直接服务 LongMemEval/LOCOMO 的路径，也是 Engram 当前已有数据模型最能支撑的方向。

### 阶段 B：再攻 hard categories

1. Graph proximity retriever。
2. Multi-hop planner 与 graph expansion 联动。
3. Temporal/as-of filtering 加强。

原因：multi-hop、multi-session、temporal 是长期记忆系统最难的类别，也是 Engram 的差异化战场。

### 阶段 C：再建长期护城河

1. Derived memory layers。
2. Reflection/experience memory。
3. Knowledge workspace diagnostics。
4. Runtime profiles。

原因：这些能力决定系统是否能从 benchmark engine 变成真正的长期记忆基础设施。

## 7. 评测设计

### 7.1 每个算法候选必须有四类结果

- Accuracy：官方 judge 或声明过的强 judge。
- Tokens：输入、输出、总 token。
- Latency：p50/p95。
- Error rate：失败、超时、解析错误。

### 7.2 每个结果表必须含 full-context baseline

如果不和同一 run 的 full-context baseline 对比，不能宣称「超过 full-context」。如果只节省 token 但准确率下降，也必须如实说明。

### 7.3 每个模块必须做 ablation

最低要求：

- baseline hybrid
- + chain-aware retrieval
- + graph proximity
- + derived summaries
- + runtime profile variant

如果收益在 run-to-run variance 以内，不能算 win。

## 8. 风险与防线

### 8.1 技术风险

- 事实抽取丢细节：用 raw chunks 保底。
- 图扩展引入 distractors：限制 hop、budget、edge confidence。
- 摘要幻觉：derived artifact 必须有 provenance。
- 时间语义混乱：区分 valid time 与 transaction time。
- profile 污染事实层：profile memory 与 semantic facts 分离。

### 8.2 许可与声誉风险

- GPLv3/不明 license 项目只做 architecture-only clean-room。
- 不搬代码、不搬 prompt、不搬 schema。
- 不用外部数字做公开 claim。
- 不说「世界第一」作为公开结论，除非有 Engram-owned benchmark evidence。

### 8.3 产品风险

- 运行 profile 太复杂：默认 `standard`，高级模式明确标注。
- 用户不信任记忆：所有回答提供 provenance。
- 记忆越积越乱：引入 salience、decay、workspace diagnostics。

## 9. 当前结论

Engram 已经具备成为领先记忆框架的核心方向：双时间轴、非破坏性冲突处理、raw + fact hybrid read path、可复现 harness。下一步不是继续堆参考项目，而是把最有把握的两个候选先打穿：

1. **Chain-aware retrieval**：把 `supersedes` 从写入元数据变成读取优势。
2. **Raw evidence fusion hardening**：把 raw chunks、facts、graph paths 的证据装配做扎实。

这两项如果能在 LongMemEval/LOCOMO 的 knowledge-update、temporal、multi-session 类别上稳定提升，同时维持低 token 和低 latency，Engram 的算法内核就会开始形成真正的领先壁垒。

## 10. 文档语言偏好

用户偏好：**后续项目内部文档优先使用中文生成**。例外情况：

- 代码标识、API 名称、命令、文件名保持原文。
- 对外英文 README、package metadata、协议/接口文档按目标读者保留英文或双语。
- 引用第三方项目名、benchmark 名称、论文名保持原文。

这条偏好不改变公开 benchmark 与 messaging 纪律：中文文档也必须遵守可复现、clean-room、无未证实宣传的原则。
