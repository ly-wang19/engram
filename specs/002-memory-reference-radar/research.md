# Memory Reference Radar

**Feature**: `002-memory-reference-radar`

**Created**: 2026-06-29

**Purpose**: Capture the public systems and algorithms Engram should study at the architecture level,
then convert the useful patterns into Engram-native, reproducible work. This is internal research
input. Public Engram claims still require Engram-owned logs and the accuracy/tokens/latency triple.

## Phase 0 Decisions

### Decision: Treat the radar as internal architecture governance, not public positioning

**Rationale**: Engram's credibility comes from reproducible results and clean ownership. The radar
should help maintainers learn quickly while preventing unsupported public comparisons or copied claims.

**Alternatives considered**:

- Public competitor matrix: rejected because it would invite stale comparisons and violate the
  project's messaging discipline unless every number is reproduced.
- Private chat notes only: rejected because architecture decisions need stable reviewable artifacts.

### Decision: Promote patterns only through Engram-native adoption candidates

**Rationale**: The useful unit is not "copy project X"; it is a candidate that maps to an Engram memory
surface, strategic bet, evaluation hypothesis, and rollback criterion.

**Alternatives considered**:

- Direct feature imports from external systems: rejected because it risks license confusion, hidden
  coupling, and features that do not match Engram's bi-temporal model.
- Pure literature survey without candidate backlog: rejected because it does not move the roadmap.

### Decision: Keep raw evidence plus consolidated memory as the default read-path assumption

**Rationale**: Engram's M1 finding says facts-only QA loses recall. Every candidate must preserve raw
session chunks, facts, and provenance unless a later benchmark proves a narrower path is better.

**Alternatives considered**:

- Facts-only memory: rejected because it drops detail and contradicts current project evidence.
- Raw-only memory: rejected because it misses conflict handling, temporal reasoning, and multi-hop
  structure.

### Decision: Make clean-room and license status part of every reference entry

**Rationale**: Engram is dual-licensed AGPL plus commercial. Architecture ideas can be studied, but
code, schemas, prompts, and benchmark artifacts must not be reused without explicit review.

**Alternatives considered**:

- Review licenses later: rejected because candidate promotion needs to know whether a source is
  architecture-only.
- Exclude copyleft projects entirely: rejected because many ideas are still safe to study at the
  architecture level.

### Decision: Prioritize candidates by benchmark category and strategic bet

**Rationale**: The goal is algorithmic leadership under reproducible evaluation. Candidates that cannot
name a target behavior, benchmark category, and accuracy/tokens/latency direction stay research-only.

**Alternatives considered**:

- Prioritize by ecosystem popularity: rejected because popularity does not prove memory quality.
- Prioritize by ease of implementation: rejected because the goal is the strongest memory engine, not
  the easiest checklist.

## Operating Rules

- Study public ideas aggressively; do not copy code without an explicit license review and clean-room
  plan.
- Treat third-party benchmark numbers as signals, not evidence. They become Engram evidence only after
  reproduction in `eval/` with committed `results/*.jsonl` logs.
- Translate every borrowed pattern into Engram's own primitives: lossless episodes, atomic facts,
  bi-temporal graph, provenance, supersedes chains, hybrid raw-chunk plus fact retrieval, salience,
  async consolidation, and harness-backed ablations.
- Do not turn this radar into public competitor positioning. Public copy should describe Engram's own
  capabilities and reproducible results.

## Review Status by Source

All entries start as architecture-only research inputs. "License pending" means no code, prompt,
schema, or benchmark artifact may be reused until a later planning step reviews the source license and
documents a clean-room plan.

| Source | Evidence status | Clean-room/license status |
| --- | --- | --- |
| Hy-Memory / Tencent Hunyuan Memory | External claims only; not reproduced in Engram harness | Architecture-only; license/source availability pending |
| Mem0 | Public repository and docs; external claims only | License review required before any code reuse |
| Graphiti / Zep | Public repository and docs; external claims only | License review required before any code reuse |
| Letta / MemGPT | Public repository and docs; external claims only | License review required before any code reuse |
| LangMem | Public repository and docs; external claims only | License review required before any code reuse |
| Cognee | Public repository and docs; external claims only | License review required before any code reuse |
| LLM Wiki | Public repository and docs; external claims only | GPLv3; architecture-only clean-room unless a later plan accepts license implications |
| Supermemory | Public repository and docs; external claims only | License review required before any code reuse |
| Hindsight | Public repository candidate; external claims only | License review and repository verification required |
| MemPalace | Public repository candidate; external claims only | License review and repository verification required |
| SimpleMem | Public repository/paper candidate; external claims only | Architecture-only until license review |
| A-Mem | Public repository/paper candidate; external claims only | Architecture-only until license review |
| HippoRAG | Public repository/paper candidate; external claims only | Architecture-only until license review |
| LightRAG | Public repository and docs; external claims only | License review required before any code reuse |
| Microsoft GraphRAG | Public repository and docs; external claims only | License review required before any code reuse |
| RAPTOR | Public repository/paper candidate; external claims only | Architecture-only until license review |
| Generative Agents | Public repository/paper candidate; external claims only | Architecture-only until license review |
| Reflexion | Public repository/paper candidate; external claims only | Architecture-only until license review |
| ExpeL | Public repository/paper candidate; external claims only | Architecture-only until license review |
| MemoryBank | Public repository/paper candidate; external claims only | Architecture-only until license review |
| MemOS | Public repository and docs; external claims only | License review required before any code reuse |
| MemoryOS | Public repository/paper candidate; external claims only | Architecture-only until license review |
| memU | Public repository and docs; external claims only | License review required before any code reuse |
| Memobase | Public repository and docs; external claims only | License review required before any code reuse |
| agentmemory | Public repository candidate; external claims only | License review and repository verification required |
| LongMemEval-V2 | Public benchmark page; external claims only | Benchmark terms and dataset license review required before reuse |
| BEAM | Public benchmark repository; external claims only | License review required before benchmark artifact reuse |
| LOCOMO | Public benchmark page and dataset; external claims only | Dataset terms and license review required before reuse |
| Agent Memory Benchmark | Public leaderboard; external claims only | Research-only; leaderboard terms review required |
| mem0 memory-benchmarks | Public benchmark repository; external claims only | Architecture-only until license and harness review |
| Awesome Agent Memory | Public literature radar; external claims only | Architecture-only; linked sources require their own license review |
| Awesome GraphMemory | Public graph-memory radar; external claims only | Architecture-only; linked sources require their own license review |
| Awesome Memory for Agents | Public literature radar; external claims only | Architecture-only; linked sources require their own license review |

## P0 References: Study First

| Source | Category | Architecture signal | Engram learning | Adoption candidate |
| --- | --- | --- | --- | --- |
| [Hy-Memory](https://hy-memory.com/) / [Tencent Hunyuan Memory](https://memory.hunyuan.tencent.com/) | Layered memory product/research system | Six-layer memory, System-1/System-2 split, evolution chains, OpenClaw plugin positioning | Confirms Engram's direction around layered memory, async consolidation, profile/mental-model layers, and supersedes chains | Chain-aware retrieval; derived layers for session summaries, mental models, and intent models; runtime profiles |
| [Mem0](https://github.com/mem0ai/mem0) | General AI memory layer | Product-grade API, user/session/agent memory, multi-signal retrieval, OpenMemory/MCP ecosystem | Study API ergonomics, entity linking, add-only extraction variants, and benchmark packaging | Memory service API refinement; entity-linked retrieval boost; OpenAI/MCP-compatible surfaces |
| [Graphiti](https://github.com/getzep/graphiti) / Zep | Temporal knowledge graph | Real-time temporal KG for agents, episode-to-entity/relation construction, temporal updates | Strong reference for temporal graph reads and update-friendly graph construction | Bi-temporal graph retrieval; temporal path expansion; graph write contracts |
| [Letta](https://github.com/letta-ai/letta) / MemGPT | Stateful agent platform | Core/archival/recall memory, agent-visible memory editing, stateful agent loops | Useful model for memory as an agent control surface, not just retrieval | Procedural memory controls; memory-edit operations with provenance and audit |
| [LangMem](https://github.com/langchain-ai/langmem) | Agent memory primitives | Semantic, episodic, and procedural memory primitives integrated with agent graphs | Good vocabulary for memory types and agent workflow integration | Typed memory API naming and graph-agent integration patterns |
| [Cognee](https://github.com/topoteretes/cognee) | Knowledge graph memory platform | Self-hosted KG memory, ingestion pipelines, Kuzu-oriented graph storage, MCP surface | Study graph ingestion shape and self-hosted deployment ergonomics | Graph backend evaluation; KG ingestion pipeline comparison |
| [Supermemory](https://github.com/supermemoryai/supermemory) | Context and memory engine | Fast local/hosted context engine, app/API packaging, cross-app memory | Reinforces context-engine product surface and raw evidence recall | Context assembly API; local-first service packaging |
| [Hindsight](https://github.com/vectorize-io/hindsight) | Learning memory for agents | "Agent memory that learns" framing, feedback-driven memory improvement | Good lens for memory as behavior improvement, not just recall | Feedback-to-memory loop; failure-derived memory candidates |
| [MemPalace](https://github.com/MemPalace/mempalace) | Open memory system | Strong emphasis on benchmarked memory and raw recall surfaces | Reinforces Engram's M1 finding that raw evidence retrieval must stay first-class | Raw-verbatim evidence retention; chunk provenance scoring |

## P1 References: Algorithm and Architecture Primitives

| Source | Category | Architecture signal | Engram learning | Adoption candidate |
| --- | --- | --- | --- | --- |
| [SimpleMem](https://github.com/aiming-lab/SimpleMem) | Lifelong memory compression | Semantic lossless compression, multimodal memory, self-evolving variants | Study recursive consolidation and query-aware compressed memory units | Sleep-time semantic compression; summary/fact/chunk fusion ablation |
| [A-Mem](https://github.com/agiresearch/a-mem) | Agentic memory | Dynamic, linked memory organization inspired by note networks | Treat memory as evolving graph of connected units, not static top-k rows | Memory-link evolution; associative expansion from retrieved units |
| [HippoRAG](https://github.com/OSU-NLP-Group/HippoRAG) | Graph RAG | Knowledge graph plus Personalized PageRank for multi-hop retrieval | High-value reference for graph-proximity scoring and multi-hop entity expansion | Graph proximity retriever; PPR-style candidate expansion |
| [LightRAG](https://github.com/HKUDS/LightRAG) | Lightweight GraphRAG | Simple and fast graph plus vector retrieval | Useful baseline for lean graph retrieval without heavy global indexing | Lightweight graph/BM25/vector fusion baseline |
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | GraphRAG pipeline | Entity/relation extraction, community summaries, local/global query modes | Reference for hierarchical graph summaries and global vs local read modes | Community summaries; global/local/hybrid graph query modes |
| [LLM Wiki](https://github.com/nashsu/llm_wiki/blob/main/README_CN.md) | Knowledge compilation workspace | Immutable sources to generated Wiki to schema/purpose, two-step ingestion, source-backed pages, four-signal graph association, Louvain communities, graph insights, hybrid retrieval | Study human-readable memory workspaces, purpose-guided consolidation, source-traceable derived pages, graph-health diagnostics, and graph-expanded retrieval | Memory workspace export; purpose-aware consolidation; graph diagnostics for sparse/bridging memories |
| [RAPTOR](https://github.com/parthsarthi03/raptor) | Hierarchical retrieval | Recursive abstractive tree-organized retrieval | Reference for hierarchical abstraction beyond session summaries | Summary tree for episode clusters; query-routed abstraction levels |
| [Generative Agents](https://github.com/joonspk-research/generative_agents) | Memory stream and reflection | Recency, relevance, importance, reflection, planning | Classic basis for salience/reflection tradeoffs | Reflection queue; salience/relevance/recency calibration |
| [Reflexion](https://github.com/noahshinn/reflexion) | Verbal reinforcement memory | Failure feedback stored as reusable verbal lessons | Useful for procedural memory and agent self-correction | Failure-to-procedure memory; task retry reflection |
| [ExpeL](https://github.com/LeapLabTHU/ExpeL) | Experiential learning | Extract insights from prior tasks and recall them for new tasks | Useful for procedural and skill memory over tasks | Experience extraction pipeline; skill/lesson retrieval |
| [MemoryBank](https://github.com/zhongwanjun/MemoryBank-SiliconFriend) | Long-term companion memory | User/persona adaptation and long-running dialogue memory | Early reference for persona persistence and user-specific memory | Profile memory update and decay baselines |

## P2 References: Product Surfaces and Deployment Patterns

| Source | Category | Architecture signal | Engram learning | Adoption candidate |
| --- | --- | --- | --- | --- |
| [MemOS](https://github.com/MemTensor/MemOS) | Memory operating system | Unified store/retrieve/manage, memory cubes, async scheduler, feedback correction, local plugin | Useful product abstraction for multi-tenant memory resources | Memory namespace/cube model; feedback correction UX |
| [MemoryOS](https://github.com/BAI-LAB/MemoryOS) | Personalized memory OS | Short/mid/long-term memory organization for personalized agents | Study memory lifecycle and personalization boundaries | Lifecycle policy for profile vs episodic vs procedural memory |
| [memU](https://github.com/NevaMind-AI/memU) | Personal memory for agents | Fast retrieval, self-evolving skills, lower-cost personal memory | Study local personal-memory ergonomics and skill evolution | Personal memory profile; skill memory surface |
| [Memobase](https://github.com/memodb-io/memobase) | Profile memory | User profile-based memory for chatbot applications | Good focused reference for profile extraction/update surfaces | Profile/identity schema and update policy |
| [agentmemory](https://github.com/rohitg00/agentmemory) | Coding-agent memory server | MCP-first coding-agent memory, lifecycle/confidence/KG/hybrid search framing | Study coding-agent install flow, MCP tooling, and repository-memory UX | MCP server ergonomics; coding-agent memory workflows |

## Benchmark and Radar Sources

| Source | Role | How Engram should use it |
| --- | --- | --- |
| [LongMemEval-V2](https://xiaowu0162.github.io/longmemeval-v2/) | Long-history agent memory benchmark | Track as a future scale target; reproduce only through in-repo harness once available |
| [BEAM](https://github.com/mohammadtavakoli78/BEAM) | Million-token long-term memory benchmark | Use for scaling and distractor robustness once M2/M3 read path is ready |
| [LOCOMO](https://snap-research.github.io/locomo/) | Long conversational memory benchmark | Use for cross-session dialogue and profile-memory evaluation |
| [Agent Memory Benchmark](https://agentmemorybenchmark.ai/) | External leaderboard/radar | Treat as ecosystem signal; do not import results as Engram claims |
| [mem0 memory-benchmarks](https://github.com/mem0ai/memory-benchmarks) | Benchmark interface reference | Study harness shape and reproducibility gaps; keep Engram harness neutral |
| [Awesome Agent Memory](https://github.com/TeleAI-UAGI/Awesome-Agent-Memory) | Literature radar | Periodic sweep source for new systems and papers |
| [Awesome GraphMemory](https://github.com/DEEP-PolyU/Awesome-GraphMemory) | Graph-memory radar | Periodic sweep source for graph-based memory techniques |
| [Awesome Memory for Agents](https://github.com/TsinghuaC3I/Awesome-Memory-for-Agents) | Agent-memory radar | Periodic sweep source for new memory-agent papers |

## Capability Patterns to Absorb

| Pattern | Best current references | Engram-native form | Evaluation hypothesis |
| --- | --- | --- | --- |
| Raw evidence as first-class memory | MemPalace, Supermemory, Mem0, agentmemory | Retrieved raw session chunks remain part of every QA read path, alongside facts and graph evidence | Improves detail recall and reduces facts-only false negatives on LongMemEval/LOCOMO |
| Evolution-chain retrieval | Hy-Memory, Graphiti, MemOS | Facts and profile records expose and retrieve `supersedes` chains with provenance and valid-time filters | Improves knowledge-update and temporal questions without increasing prompt noise |
| Temporal graph expansion | Graphiti, HippoRAG, LightRAG, GraphRAG | Query entities seed n-hop/PPR-style graph expansion over bi-temporal relations | Improves multi-hop/multi-session categories while keeping token budget below full context |
| Hierarchical consolidation | RAPTOR, GraphRAG, SimpleMem, Hy-Memory | Session summaries, profile summaries, mental models, and community summaries are derived artifacts with source links | Reduces tokens while preserving answerable evidence for long histories |
| Knowledge workspace and diagnostics | LLM Wiki, GraphRAG, Cognee | Optional human-readable memory workspace with purpose, source-backed derived pages, graph communities, sparse areas, bridge nodes, and missing-link diagnostics | Improves maintainability, auditability, and consolidation quality without treating workspace pages as untraceable truth |
| Agentic reflection and experience memory | Generative Agents, Reflexion, ExpeL, A-Mem, Hindsight | Failures, repeated tasks, and high-salience episodes produce procedural lessons and linked memory updates | Improves repeated-task agent behavior and procedural recall without polluting factual memory |
| Runtime profiles | Hy-Memory, MemOS, Mem0, Supermemory | Configurable modes such as `lite`, `standard`, `graph`, and `consolidated`, each with measured tradeoffs | Lets users choose latency/cost/accuracy tradeoffs and lets the harness compare ablations fairly |
| Memory feedback/correction | MemOS, memU, Letta, Mem0 | User or agent corrections update memory non-destructively with provenance and invalidation | Improves correction handling while preserving auditability |
| Profile/identity memory | Memobase, MemoryBank, Mem0, Hy-Memory | Identity/profile records are distinct typed memory with update, conflict, and decay rules | Improves personalization questions without mixing preference facts into raw episodic recall |

## Staged Assimilation Path

### Stage 0 - Governance and Radar

- Keep this radar current and reviewed before each major memory feature.
- Require source link, license status, clean-room note, and evaluation hypothesis for every promoted
  candidate.
- Do not implement from this radar directly; promote a candidate into a normal Spec-Kit plan first.

### Stage 1 - Evidence-First Read Path

- Promote raw evidence retrieval and chunk provenance as a non-negotiable read-path invariant.
- Add chain-aware retrieval for facts/profile records so updates are explainable.
- Target benchmark behaviors: detail recall, knowledge update, temporal current-vs-past questions.

### Stage 2 - Derived Memory Layers

- Add or formalize session summary, profile summary, mental model, and intent/procedure layers as
  derived artifacts.
- Every derived artifact must point back to raw episodes/facts; no summary becomes untraceable truth.
- Study LLM Wiki-style purpose files and source-backed workspace pages as an optional, human-readable
  view over Engram memory, not as a replacement for typed memory.
- Target benchmark behaviors: long-session compression, profile consistency, repeated preference recall.

### Stage 3 - Graph Multi-Hop Retrieval

- Add graph proximity retrieval with controlled n-hop expansion and time filtering.
- Test lightweight graph/vector/BM25 fusion before adding heavier community-summary machinery.
- Target benchmark behaviors: multi-hop, multi-session, entity-chain questions.

### Stage 4 - Reflection and Experience Memory

- Convert failures, repeated actions, and reinforced facts into procedural lessons when evidence is
  strong enough.
- Keep procedural lessons separate from factual claims and require provenance.
- Target benchmark behaviors: agent task improvement and repeated workflow consistency.

### Stage 5 - Runtime Profiles and Scoreboard

- Define user-selectable profiles with measured accuracy/tokens/latency tradeoffs.
- Publish only Engram-owned numbers with raw logs and full-context baselines.
- Target outcome: trusted open scoreboard plus a memory engine that can be run locally, inspected, and
  reproduced.

## Promotion Checklist for Any Candidate

- Source is public and link is recorded.
- License is reviewed, or candidate is architecture-only clean-room.
- Candidate maps to at least one Engram strategic bet.
- Candidate preserves raw evidence plus fact retrieval.
- Candidate preserves bi-temporal provenance and non-destructive invalidation when it touches facts.
- Candidate has a benchmark category and an accuracy/tokens/latency hypothesis.
- Candidate has an ablation plan and rollback criterion.
- Public messaging impact is either none or tied to Engram-owned results.

## Initial Priority Queue

1. **Chain-aware retrieval**: when a fact/profile record is retrieved, optionally include its
   `supersedes` chain and provenance under a token budget.
2. **Raw evidence fusion hardening**: make raw chunks, facts, and graph paths explicit evidence classes
   in context assembly.
3. **Derived memory layers**: specify session summaries, profile summaries, mental models, and intent or
   procedure records as typed derived artifacts.
4. **Memory workspace diagnostics**: define optional source-backed pages, purpose context, sparse-area
   warnings, bridge nodes, and missing-link suggestions for maintainers.
5. **Graph proximity retriever**: add a lightweight n-hop/PPR-inspired retrieval component behind the
   graph-store interface.
6. **Reflection/experience memory**: create a procedural-memory path for failures, repeated tasks, and
   user corrections.
7. **Runtime profiles**: define measured modes that trade off latency, tokens, and recall.

## Open Questions for Later Planning

- Which benchmark slice should be the first gate for chain-aware retrieval: LongMemEval knowledge
  update, LOCOMO temporal questions, or a small synthetic update set?
- Should mental models and intent models be separate typed entities, or derived profile records with a
  stricter schema?
- What is the minimum graph-expansion algorithm that can improve multi-hop recall without adding heavy
  dependencies or violating the zero-setup invariant?
- Should a human-readable workspace be generated as Markdown pages, JSON artifacts, or both, and how do
  we guarantee those pages never become untraceable source-of-truth records?
- How should user corrections be represented so they can invalidate prior claims without turning every
  correction into a high-salience permanent fact?
