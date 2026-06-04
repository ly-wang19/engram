"""The public facade. Wires System-1 ingest, System-2 consolidation, and the hybrid + multi-hop read
path behind a small API: add() / consolidate() / search() / as_of() / history() / profile().

Defaults are fully offline (hashing embedder, rule extractor, in-memory stores) so `Memory()` works with
zero setup. Pass a real `embedder` / `llm` / store factories to run on benchmark backends.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import Config
from .consolidate import ConsolidationEngine, reinforce
from .consolidate.llm_extractor import EXTRACT_SYSTEM
from .consolidate.summarizer import (
    PERSONA_SYSTEM,
    SESSION_SUMMARY_SYSTEM,
    ProfileBuilder,
    SessionSummarizer,
)

# The editable memory-policy prompts (the console's "提示词" / "要记录什么记忆"). Defaults are the
# built-in prompts; a per-user override (empty string = use default) is stored in Memory.policy.
POLICY_DEFAULTS = {
    "extract_instruction": "",  # additive directive: what to record / what to ignore
    "extract_system": EXTRACT_SYSTEM,
    "summary_system": SESSION_SUMMARY_SYSTEM,
    "persona_system": PERSONA_SYSTEM,
}
from .embed import Embedder, HashingEmbedder
from .ingest import IdentityResolver, Ingestor
from .llm import LLM
from .retrieve import HybridRetriever, MultiHopPlanner, history
from .store import (
    GraphStore,
    InMemoryDocStore,
    InMemoryGraphStore,
    InMemoryVectorStore,
    VectorStore,
)
from .retrieve.lexical import bm25_scores, overlap_terms
from .types import Episode, Fact, WorkingMemory
from .util import fmt_date, now

# words too generic to confirm an attribute on their own ("favorite food" must not match
# "favorite programming language" just because both contain "favorite").
_GENERIC_ATTR_TERMS = {"favorite", "favourite", "name", "is", "are", "of", "the"}


@dataclass
class SearchResult:
    query: str
    facts: list[Fact] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    via: str = "hybrid"  # "hybrid" | "multi-hop" | "abstain"
    abstained: bool = False
    _answer: Optional[str] = None

    def answer(self) -> str:
        if self.abstained:
            return "I don't have that in memory."
        if self._answer is not None:
            return self._answer
        if not self.facts:
            return "I don't have that in memory."
        top = self.facts[0]
        return top.object or top.text

    def top(self) -> Optional[Fact]:
        return self.facts[0] if self.facts else None


class Memory:
    def __init__(
        self,
        config: Optional[Config] = None,
        embedder: Optional[Embedder] = None,
        llm: Optional[LLM] = None,
        reranker=None,
        vector_store_factory: Callable[[], VectorStore] = InMemoryVectorStore,
        graph_store_factory: Callable[[], GraphStore] = InMemoryGraphStore,
    ) -> None:
        self.config = config or Config()
        self.embedder = embedder or HashingEmbedder(self.config.embed_dim)
        self.reranker = reranker  # optional cross-encoder; sharpens chunk/session retrieval (CLAUDE.md L1)
        self.llm = llm  # used by agentic retrieval (query decomposition) when enabled

        self.episodes_doc = InMemoryDocStore()
        self.episodes_vec = vector_store_factory()
        self.fact_store = vector_store_factory()  # HOT tier: the fast, frequently-retrieved working set
        self.cold_store = vector_store_factory()  # COLD tier: aged-out facts, preserved (never deleted)
        self.summary_vec = vector_store_factory()  # L2 session summaries, retrievable for a lean read slice
        self.graph = graph_store_factory()
        self.resolver = IdentityResolver()

        self.summarizer = SessionSummarizer(llm)
        self.profiles = ProfileBuilder()
        self._persona_cache: dict[str, str] = {}
        # Working memory: the small, currently-attended set assembled for the latest query (the OS-paging
        # "hot" context). Populated by lean_context; inspectable, transient — distinct from the durable stores.
        self.working_set: list[Fact] = []
        # WORKING MEMORY tier: ephemeral, session/TTL-scoped state that is deliberately kept OUT of the
        # durable long-term store (CLAUDE.md §3 typed memory). Lifecycle-managed (expire/consume/clear).
        self.working_mem: dict[str, WorkingMemory] = {}
        # User-customized focus areas (the "关注点" panel). NOT cosmetic — genuinely wired:
        #   * track: topics the user wants emphasized -> salience boost, which is a first-class retrieval
        #     scoring signal (CLAUDE.md §3.3 w_sal) AND exempts them from decay/eviction (they stay hot).
        #   * mute: topics to suppress -> filtered out of the assembled read context and the persona.
        self.focus: dict[str, list[str]] = {"track": [], "mute": []}
        # Per-user memory policy: editable prompts + a "what to record" directive (the console's 记忆策略
        # page). Empty string for a prompt means "use the built-in default". Wired into the real extractor /
        # summarizer / persona via _apply_policy(), and persisted with the snapshot.
        self.policy: dict[str, str] = {"extract_instruction": "", "extract_system": "",
                                       "summary_system": "", "persona_system": ""}
        self._persist_path: Optional[str] = None
        self._rewire()

    def _rewire(self) -> None:
        """(Re)bind the pipeline components to the current stores. Called at init and after loading a
        snapshot, so persistence can swap the stores in without touching the embedder/llm (which aren't
        serialized)."""
        self.ingestor = Ingestor(self.episodes_doc, self.episodes_vec, self.embedder, self.resolver)
        self.engine = ConsolidationEngine(self.fact_store, self.graph, self.embedder, self.config, self.llm)
        self.retriever = HybridRetriever(self.fact_store, self.graph, self.embedder, self.config)
        self.planner = MultiHopPlanner(self.graph, self.fact_store, self.config)

    # --- persistence (so memory survives across processes/sessions; CLAUDE.md §6) ---
    def save(self, path: Optional[str] = None) -> None:
        """Snapshot the durable stores to disk. The embedder/llm are NOT serialized — they're re-attached
        when you reopen. Plain dataclasses + dicts, so it's a single pickle."""
        import os
        import pickle

        path = path or self._persist_path
        if not path:
            raise ValueError("no path to save to")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as fh:
            # Dict format (was a fixed tuple): lets us add fields like `focus` without breaking old
            # snapshots. open() reads both shapes.
            pickle.dump({
                "episodes_doc": self.episodes_doc, "episodes_vec": self.episodes_vec,
                "fact_store": self.fact_store, "cold_store": self.cold_store,
                "summary_vec": self.summary_vec, "graph": self.graph,
                "resolver": self.resolver, "persona_cache": self._persona_cache,
                "focus": self.focus, "policy": self.policy, "working_mem": self.working_mem,
            }, fh)

    @classmethod
    def open(cls, path: str, **kwargs) -> "Memory":
        """Open a persistent Memory: load the snapshot at `path` if it exists, else start fresh. Pass the
        same `embedder` / `llm` you want to use (they're not stored). Call `.save()` after writes."""
        import os
        import pickle

        mem = cls(**kwargs)
        if os.path.exists(path):
            with open(path, "rb") as fh:
                blob = pickle.load(fh)
            if isinstance(blob, dict):  # current format
                mem.episodes_doc = blob["episodes_doc"]; mem.episodes_vec = blob["episodes_vec"]
                mem.fact_store = blob["fact_store"]; mem.cold_store = blob["cold_store"]
                mem.summary_vec = blob["summary_vec"]; mem.graph = blob["graph"]
                mem.resolver = blob["resolver"]; mem._persona_cache = blob["persona_cache"]
                mem.focus = blob.get("focus") or {"track": [], "mute": []}
                mem.policy = blob.get("policy") or {"extract_instruction": "", "extract_system": "",
                                                    "summary_system": "", "persona_system": ""}
                mem.working_mem = blob.get("working_mem") or {}
            else:  # legacy 8-tuple snapshot (pre-focus)
                (mem.episodes_doc, mem.episodes_vec, mem.fact_store, mem.cold_store,
                 mem.summary_vec, mem.graph, mem.resolver, mem._persona_cache) = blob
            mem._rewire()
            mem._classify()  # backfill category/sensitivity on facts saved before feature ⑤
        mem._persist_path = path
        return mem

    # --- write path ---
    def add(
        self,
        content: str,
        user_id: str = "default",
        session_id: str = "default",
        speaker: str = "user",
        event_time: Optional[float] = None,
        consolidate: bool = False,
        embedding: Optional[list] = None,
    ) -> Episode:
        ep = self.ingestor.ingest(content, user_id, session_id, speaker, event_time, embedding=embedding)
        if consolidate:
            self.consolidate()
        return ep

    def remember(self, content: str, user_id: str = "default", session_id: str = "default",
                 scope: str = "auto") -> dict:
        """High-level write with ephemeral routing. KEY: it ALWAYS stores the lossless, dated episode, so
        'when did X happen?' is answerable from history regardless of routing. For transient STATE
        ('today my throat hurts') it additionally adds a working-memory item AND marks the episode so no
        durable profile FACT is extracted from it — the *event* is remembered (dated, retrievable), but
        the *state* never lingers as a current profile attribute. Durable content is left pending for the
        caller to consolidate() into long-term facts. Returns a dict describing the routing.

        This is the corrected model: ephemeral != deleted. Only the durable-profile promotion is skipped;
        the episodic record (CLAUDE.md L0) is always kept."""
        ephemeral = scope == "working" or (scope == "auto" and self.is_ephemeral(content))
        ep = self.add(content, user_id=user_id, session_id=session_id)
        if ephemeral:
            ep.consolidated = True  # stays in the dated episodic log, but yields no durable fact
            ep.metadata["ephemeral"] = True
            wm = self.remember_working(content, user_id=user_id, session_id=session_id)
            return {"scope": "working", "episode_id": ep.id, "working_id": wm.id, "kind": wm.kind}
        return {"scope": "long", "episode_id": ep.id}

    def consolidate(self, episodes: Optional[list[Episode]] = None) -> dict[str, int]:
        """System-2: extract facts + build the bi-temporal graph from `episodes` (default: all pending).
        Invalidates the persona cache since the live fact set just changed."""
        eps = episodes if episodes is not None else self.ingestor.pending()
        self._apply_policy()  # honor the user's editable extraction prompt / "what to record" directive
        self.sweep_working()  # housekeeping: drop expired/consumed ephemeral items
        stats = self.engine.consolidate(eps)
        self._classify()  # feature ⑤: tag new facts with a category + sensitivity flag (rule-based)
        self._persona_cache.clear()  # facts changed -> any cached persona is stale
        return stats

    def consolidate_full(
        self,
        fact_episodes: Optional[list[Episode]] = None,
        summary_episodes: Optional[list[Episode]] = None,
    ) -> dict[str, int]:
        """One coherent System-2 pass building all read-time layers:
          L1 facts   from `fact_episodes`    (deep extraction over the most relevant sessions),
          L2 summaries from `summary_episodes` (broad, cheap coverage for aggregation),
          L3 persona  refreshed from the resulting facts (lazily, on first read).
        The two episode sets differ by design: facts want depth on a few sessions, summaries want breadth.
        Defaults to the pending queue for both when not specified."""
        stats = self.consolidate(fact_episodes)
        stats["summaries"] = self.summarize_episodes(
            summary_episodes if summary_episodes is not None else self.ingestor.pending()
        )
        # Reflector: propagate any post-summary knowledge-updates into the L2 layer so the lean read
        # never surfaces a stale value that a later fact already corrected.
        stats["reflected"] = sum(self.reflect(uid) for uid in {
            ep.user_id for ep in (summary_episodes or self.episodes_doc.values())})
        return stats

    def link_identity(self, a: str, b: str) -> str:
        return self.resolver.link(a, b)

    # --- user-authored memory management (the editable layer the management UI drives) ---
    def add_fact(self, subject: str, predicate: str, object: str, user_id: str = "default",
                 valid_at: Optional[float] = None) -> Fact:
        """Manually assert a fact. It is marked source='user' (authoritative): conflict resolution lets it
        supersede any extracted value on the same slot, and it is then protected from future auto-overrides."""
        user = self.resolver.resolve(user_id)
        f = Fact(subject=subject, predicate=predicate, object=object, user_id=user, source="user",
                 valid_at=valid_at if valid_at is not None else now())
        f.embedding = self.embedder.embed(f.text)
        from .consolidate.classify import classify_fact
        classify_fact(f)  # tag category + sensitivity (feature ⑤)
        live = [x for x in self.fact_store.values() if x.user_id == user and x.is_live()]
        action, invalidated = self.engine.conflict.reconcile(f, live)
        for old in invalidated:
            self.engine.graph_builder.invalidate(old.id, f.created_at)
        if action != "duplicate":
            self.fact_store.upsert(f.id, f.embedding, f)
            self.engine.graph_builder.add_fact(f)
        self._persona_cache.clear()
        return f

    def update_fact(self, fact_id: str, subject: Optional[str] = None, predicate: Optional[str] = None,
                    object: Optional[str] = None, sensitive: Optional[bool] = None,
                    category: Optional[str] = None) -> Optional[Fact]:
        """Edit a fact's fields in place and mark it user-authored (so auto-extraction won't revert it).
        Re-classifies category/sensitivity from the new content; an explicit `sensitive`/`category`
        overrides the auto result (user's call always wins)."""
        f = self.fact_store.get(fact_id) or self.cold_store.get(fact_id)
        if f is None:
            return None
        if subject is not None:
            f.subject = subject
        if predicate is not None:
            f.predicate = predicate
        if object is not None:
            f.object = object
        f.text = f"{f.subject} {f.predicate.replace('_', ' ')} {f.object}".strip()
        f.embedding = self.embedder.embed(f.text)
        f.source = "user"
        f.invalid_at = None  # a user edit makes it current again
        f.expired_at = None
        # re-classify from the edited content, then apply any explicit user override
        from .consolidate.classify import classify
        f.category, f.sensitive = classify(f.predicate, f.object, f.text)
        if category is not None:
            f.category = category
        if sensitive is not None:
            f.sensitive = sensitive
        self.fact_store.upsert(f.id, f.embedding, f)
        self._persona_cache.clear()
        return f

    def delete_fact(self, fact_id: str) -> bool:
        """Right-to-forget: HARD-remove a fact (distinct from auto-invalidation, which keeps history). This
        is user-initiated erasure, so the data is actually gone — from both the hot and cold tiers."""
        existed = self.fact_store.get(fact_id) is not None or self.cold_store.get(fact_id) is not None
        self.fact_store.delete(fact_id)
        self.cold_store.delete(fact_id)
        self._persona_cache.clear()
        return existed

    # --- focus areas: the "关注点" customization (what memory should emphasize / suppress) ---
    def set_focus(self, track: Optional[list[str]] = None, mute: Optional[list[str]] = None) -> dict:
        """Customize what memory prioritizes. Real wiring, not a label:
          * `track` topics get a salience boost — and salience is a first-class retrieval-scoring signal
            (CLAUDE.md §3.3 w_sal) and a decay/eviction exemption, so tracked topics genuinely rank higher
            and stay in the hot tier.
          * `mute` topics are suppressed from the assembled read context (lean_context) and the persona.
        Passing None leaves that list unchanged; passing [] clears it."""
        if track is not None:
            self.focus["track"] = [t.strip() for t in track if t.strip()]
        if mute is not None:
            self.focus["mute"] = [m.strip() for m in mute if m.strip()]
        self.apply_focus()
        self._persona_cache.clear()
        return self.get_focus()

    def get_focus(self) -> dict:
        return {"track": list(self.focus.get("track", [])), "mute": list(self.focus.get("mute", []))}

    @staticmethod
    def _matches(f: Fact, terms: list[str]) -> bool:
        if not terms:
            return False
        hay = f.text.lower()
        return any(t.lower() in hay for t in terms)

    def apply_focus(self, boost: float = 0.5, cap: float = 5.0) -> int:
        """Boost the salience of every stored fact matching a tracked topic so the user's declared
        priorities actually rank higher and resist decay/eviction. Capped so repeated edits saturate
        instead of inflating without bound. Returns the number of facts boosted."""
        track = self.focus.get("track", [])
        if not track:
            return 0
        n = 0
        for f in list(self.fact_store.values()) + list(self.cold_store.values()):
            if self._matches(f, track):
                f.salience = min(cap, f.salience + boost)
                n += 1
        return n

    def graph_data(self, user_id: str = "default", as_of: Optional[float] = None) -> dict:
        """Export the semantic graph as nodes + edges for the 关系图谱 visualization. Entities are nodes;
        relations are edges carrying their predicate and bi-temporal (live/superseded) status. Orphan
        entities (no surviving edge) are dropped so the picture stays about relationships."""
        user = self.resolver.resolve(user_id)
        ents = {e.id: e for e in self.graph.entities.values() if e.user_id == user}
        edges, touched = [], set()
        for r in self.graph.relations():
            if r.subject_id not in ents or r.object_id not in ents:
                continue
            live = r.invalid_at is None or (as_of is not None and r.invalid_at > as_of)
            edges.append({"source": r.subject_id, "target": r.object_id,
                          "predicate": r.predicate.replace("_", " "), "live": live})
            touched.add(r.subject_id)
            touched.add(r.object_id)
        nodes = [{"id": eid, "name": ents[eid].name, "type": ents[eid].type} for eid in touched]
        return {"nodes": nodes, "edges": edges}

    # --- memory policy: editable prompts + a "what to record" directive (the 记忆策略 page) ---
    def get_policy(self) -> dict:
        """Return the user's overrides AND the built-in defaults, so the console can show what the
        effective prompt is and let the user edit from it."""
        return {"policy": dict(self.policy), "defaults": dict(POLICY_DEFAULTS)}

    def set_policy(self, **fields: str) -> dict:
        """Update policy fields (extract_instruction / extract_system / summary_system / persona_system).
        An empty string clears an override (falls back to the default). Applied immediately to the
        extractor/summarizer so the very next consolidation obeys it."""
        for k, v in fields.items():
            if k in self.policy and v is not None:
                self.policy[k] = v
        self._apply_policy()
        self._persona_cache.clear()
        return self.get_policy()

    def _effective(self, key: str) -> str:
        """The override if set, else the built-in default."""
        return self.policy.get(key) or POLICY_DEFAULTS[key]

    def _apply_policy(self) -> None:
        """Push the current policy into the live extractor + summarizer. Called before each consolidation
        / summarization / persona build, and whenever the policy changes. Guarded so the offline
        RuleExtractor (no editable prompt) is simply left alone."""
        ex = getattr(self.engine, "extractor", None)
        if ex is not None and hasattr(ex, "system") and hasattr(ex, "instruction"):
            ex.system = self._effective("extract_system")
            ex.instruction = self.policy.get("extract_instruction", "")
        self.summarizer.system = self._effective("summary_system")

    # --- WORKING MEMORY tier: ephemeral, session/TTL-scoped state kept OUT of long-term (feature ①) ---
    # Markers that a statement is transient (a passing state/intent) rather than a durable fact — used to
    # route "today my throat hurts" to working memory instead of polluting the long-term store.
    _EPHEMERAL_MARKERS = (
        "today", "right now", "currently", "this morning", "this afternoon", "tonight", "this week",
        "for now", "at the moment", "temporarily", "this trip", "feeling a bit", "i feel ",
        "今天", "现在", "此刻", "暂时", "这会儿", "待会", "等下", "本次", "这趟", "这次", "最近想", "改天",
    )

    @classmethod
    def is_ephemeral(cls, content: str) -> bool:
        """Heuristic router: does this read as transient state/intent (→ working memory) rather than a
        durable fact (→ long-term)? Deterministic and free; the caller can always override with an explicit
        scope. Keeping transient context out of long-term is the general memory-hygiene win."""
        c = content.lower()
        return any(m in c for m in cls._EPHEMERAL_MARKERS)

    def remember_working(self, content: str, user_id: str = "default", session_id: str = "default",
                         kind: str = "state", ttl_seconds: Optional[float] = None,
                         event_time: Optional[float] = None) -> WorkingMemory:
        """Store an ephemeral item in the working-memory tier. NOT consolidated into long-term and NOT part
        of the durable profile. `ttl_seconds` sets a hard wall-clock expiry; otherwise it lives until the
        session is cleared."""
        user = self.resolver.resolve(user_id)
        wm = WorkingMemory(
            content=content, user_id=user, session_id=session_id, kind=kind,
            event_time=event_time if event_time is not None else now(),
            expires_at=(now() + ttl_seconds) if ttl_seconds else None,
        )
        wm.embedding = self.embedder.embed(content)
        self.working_mem[wm.id] = wm
        return wm

    def working_memory(self, user_id: str = "default", session_id: Optional[str] = None,
                       as_of: Optional[float] = None, kind: Optional[str] = None) -> list[WorkingMemory]:
        """Live working-memory items (optionally scoped to a session / kind); expired & consumed excluded.
        Lazily sweeps hard-expired items on read."""
        user = self.resolver.resolve(user_id)
        self.sweep_working(as_of)
        return [w for w in self.working_mem.values()
                if w.user_id == user and w.is_live(as_of, session_id) and (kind is None or w.kind == kind)]

    def clear_session(self, user_id: str = "default", session_id: str = "default") -> int:
        """End-of-session / power-cycle clear: drop this session's working memory. Returns count cleared."""
        user = self.resolver.resolve(user_id)
        ids = [i for i, w in self.working_mem.items() if w.user_id == user and w.session_id == session_id]
        for i in ids:
            del self.working_mem[i]
        return len(ids)

    def consume_working(self, wm_id: str) -> bool:
        """Soft-clear: mark a working item consumed so it stops surfacing (it served its purpose)."""
        w = self.working_mem.get(wm_id)
        if w is None:
            return False
        w.consumed = True
        return True

    def sweep_working(self, as_of: Optional[float] = None) -> int:
        """Drop hard-expired / consumed working items. Called lazily on read and during consolidate."""
        t = now() if as_of is None else as_of
        dead = [i for i, w in self.working_mem.items()
                if w.consumed or (w.expires_at is not None and w.expires_at <= t)]
        for i in dead:
            del self.working_mem[i]
        return len(dead)

    # --- classification + sensitivity (feature ⑤) ---
    def _classify(self) -> None:
        """Tag each fact with a coarse category + sensitivity flag (rule-based, idempotent)."""
        from .consolidate.classify import classify_fact
        for f in self.fact_store.values():
            classify_fact(f)
        for f in self.cold_store.values():
            classify_fact(f)

    # --- L2/L3 abstraction (built during consolidation, stored for a lean read) ---
    def summarize_episodes(self, episodes: list[Episode]) -> int:
        """L2: distill each episode into a compact summary and index it in summary_vec for retrieval.
        Summaries are generated in parallel (independent LLM calls) then batch-embedded — so a lean read
        can pull a few session digests instead of dragging whole raw sessions into context."""
        from concurrent.futures import ThreadPoolExecutor

        self._apply_policy()  # honor the user's editable summary prompt
        todo = [ep for ep in episodes if not ep.summary]
        if not todo:
            return 0
        if self.llm is not None and len(todo) > 1:
            import os
            _sw = int(os.environ.get("ENGRAM_SUMMARIZE_WORKERS", "8"))
            with ThreadPoolExecutor(max_workers=min(_sw, len(todo))) as pool:
                summaries = list(pool.map(self.summarizer.summarize, todo))
        else:
            summaries = [self.summarizer.summarize(ep) for ep in todo]
        vecs = self.embedder.embed_batch([s or ep.content[:200] for s, ep in zip(summaries, todo)])
        for ep, summ, vec in zip(todo, summaries, vecs):
            ep.summary = summ
            ep.summary_embedding = vec
            self.summary_vec.upsert(ep.id, vec, ep)
        return len(todo)

    def retrieve_summaries(self, query: str, user_id: str = "default", k: int = 8) -> list[Episode]:
        """Top-k session summaries for a query, via the SAME hybrid (dense + BM25, RRF) signal as fact and
        episode retrieval. Lexical matching matters here: aggregation questions ('how many trips') hinge on
        exact terms a summary mentions, which a pure-embedding lookup can rank below a vaguely-similar one.
        Returns episodes carrying the .summary field."""
        user = self.resolver.resolve(user_id)
        pool = max(k * 3, 30)
        cands = self.summary_vec.search(self.embedder.embed(query), pool, where=lambda e: e.user_id == user)
        eps = [ep for _, ep in cands]
        if len(eps) <= k:
            return eps
        from .retrieve.hybrid import date_terms
        bm25 = bm25_scores(query, [
            (ep.id, f"{ep.summary or ep.content} {date_terms(ep.event_time)}") for ep in eps])
        if bm25:
            bm25_rank = {eid: r for r, (eid, _) in
                         enumerate(sorted(bm25.items(), key=lambda x: x[1], reverse=True))}
            K = 60  # standard RRF constant
            order = sorted(range(len(eps)), key=lambda i: -(
                1.0 / (K + i + 1) + 1.0 / (K + bm25_rank.get(eps[i].id, len(eps)) + 1)))
            eps = [eps[i] for i in order]
        return eps[:k]

    def reflect(self, user_id: str = "default") -> int:
        """Reflector (Mastra-style summary maintenance, CLAUDE.md §3). An L2 summary is frozen when its
        session is summarized; if a fact it states is SUPERSEDED later, the stale value lingers in the
        summary text and the lean read would surface it. This appends the current value to any summary
        whose source facts were invalidated, so knowledge-updates propagate into the abstraction layer.
        Returns the number of summaries corrected."""
        user = self.resolver.resolve(user_id)
        facts = [f for f in self.fact_store.values() if f.user_id == user]
        replacement = {f.supersedes: f for f in facts if f.supersedes and f.is_live()}
        by_episode: dict[str, list] = {}
        for old in facts:
            if old.is_live() or old.id not in replacement:
                continue
            for ep_id in old.provenance:
                by_episode.setdefault(ep_id, []).append(old)
        corrected = 0
        for ep in self.summary_vec.values():
            if ep.user_id != user or "[updated:" in (ep.summary or ""):
                continue
            stale = by_episode.get(ep.id)
            if not stale:
                continue
            current = "; ".join(replacement[o.id].text for o in stale if o.id in replacement)
            if current:
                ep.summary = f"{ep.summary or ''} [updated: {current}]".strip()
                corrected += 1
        return corrected

    # Procedural memory: how-to / instruction knowledge — the rules the user has stated for how things
    # should be done ("always remind me…", "I prefer responses in bullet points"). A distinct typed view
    # over the fact store (CLAUDE.md §3 typed memory), surfaced so the assistant follows standing instructions.
    _INSTRUCTION_PREDS = frozenset({
        "wants", "wants_reminder", "instruction", "prefers", "prefers_format", "asks_to", "always",
        "never", "remind", "rule", "routine", "how_to", "procedure", "wants_me_to",
    })

    def evict_cold(self, max_hot: int) -> int:
        """Heat-tiered paging (MemoryOS, CLAUDE.md §3 / Bet E). When the HOT fact set exceeds capacity,
        page the COLDEST facts (lowest salience, then oldest access) to the cold tier — EXCEPT durable
        identity/preference facts, which stay hot. NON-DESTRUCTIVE: cold facts are moved, not deleted, so
        history and as-of queries are intact and a future query can still page them back. This is what
        keeps retrieval cost O(hot working set) instead of O(all history) at the 10M-token frontier.
        Returns the number paged out."""
        from .consolidate.decay import is_durable

        hot = self.fact_store.values()
        if len(hot) <= max_hot:
            return 0
        evictable = [f for f in hot if not is_durable(f.predicate)]
        evictable.sort(key=lambda f: (f.salience, f.last_access))  # coldest (lowest salience/oldest) first
        n_out = min(len(evictable), len(hot) - max_hot)
        for f in evictable[:n_out]:
            self.cold_store.upsert(f.id, f.embedding or [], f)  # preserve in cold tier
            self.fact_store.delete(f.id)  # remove from hot index only
        return n_out

    def procedural(self, user_id: str = "default") -> list[Fact]:
        """Standing instructions / how-to facts for this user (procedural memory)."""
        user = self.resolver.resolve(user_id)
        return [f for f in self.fact_store.values()
                if f.user_id == user and f.is_live()
                and (f.predicate.lower() in self._INSTRUCTION_PREDS
                     or any(f.predicate.lower().startswith(p + "_") for p in ("wants", "prefers", "remind")))]

    def structured_profile(self, user_id: str = "default") -> dict:
        """L2 structured profile: the user's live facts grouped into basic info / preferences / habits,
        split into confirmed vs tentative for DISPLAY. This is a read-only derived view — it never filters
        the fact store or the retrieval path, so recall is unaffected (search/lean_context see all facts)."""
        from .consolidate.structured import build_structured_profile
        user = self.resolver.resolve(user_id)
        subject = self.engine.self_name(user)
        live = [f for f in self.fact_store.values() if f.user_id == user and f.is_live()]
        return build_structured_profile(live, subject, user)

    def build_persona(self, user_id: str = "default") -> str:
        """L3: a compact narrative profile (preferences/habits/possessions) synthesized from live facts."""
        user = self.resolver.resolve(user_id)
        if user in self._persona_cache:
            return self._persona_cache[user]
        subject = self.engine.self_name(user)
        mute = self.focus.get("mute", [])
        live = [f for f in self.fact_store.values()
                if f.user_id == user and f.is_live() and not self._matches(f, mute)]
        persona = (self.profiles.narrative(subject, live, llm=self.llm,
                                           system=self._effective("persona_system")) if live else "")
        track = self.focus.get("track", [])
        if track:  # surface the user's declared priorities in their profile
            line = "FOCUS AREAS (user asked to prioritize): " + ", ".join(track)
            persona = (persona + "\n" + line).strip() if persona else line
        self._persona_cache[user] = persona
        return persona

    def lean_context(
        self,
        query: str,
        user_id: str = "default",
        as_of: Optional[float] = None,
        top_k: Optional[int] = None,
        n_summaries: int = 20,
        n_facts: int = 15,
        n_chunks: int = 2,
        persona: bool = True,
        agentic: bool = False,
        cascade: bool = False,  # _S-optimal off; it's the _M/10M scaling primitive (coarse->fine drill)
        timeline: bool = False,  # add a chronological event timeline (helps temporal ordering/durations)
        char_budget: int = 60_000,
        session_id: Optional[str] = None,  # when set, prepend this session's ephemeral working memory
        redact_sensitive: bool = False,  # drop sensitive facts (feature ⑤) — for shared/export contexts
    ) -> str:
        """The scalable read path (CLAUDE.md Bet A/E): assemble a SMALL, well-organized context from
        retrieved abstractions instead of the whole history —
            L3 persona  +  L1 dated facts  +  L2 session summaries  +  a couple full chunks for detail.

        Tokens stay roughly constant as history grows (a fixed-size slice is retrieved), which full-context
        cannot do. Two design points that make this both lean and accurate:
          * The top-`n_chunks` sessions are shown in FULL (detail) and EXCLUDED from the summary block, so
            no session appears twice — every token buys new information.
          * The summary block carries broad chronological COVERAGE (default 20), which is what aggregation
            questions ('how many trips', 'list everything') need; summaries are tiny so coverage stays cheap.
        `char_budget` hard-caps the assembled context so it can never approach the full-history size."""
        user = self.resolver.resolve(user_id)
        blocks: list[str] = []

        # Bet B — multi-hop decomposition. For a relational/aggregation question, an LLM splits it into
        # sub-queries ('who is my colleague' + 'where does <colleague> work'); we then retrieve facts AND
        # summaries for each angle and union them, so 2nd-hop evidence that the single query can't surface
        # gets pulled in. This is the field's weak spot (multi-session/multi-hop) and our attack surface.
        queries = [query]
        if agentic and self.llm is not None:
            from .retrieve.agentic import AgenticRetriever
            queries += AgenticRetriever(self, self.llm)._subqueries(query)

        # The L3 persona is a free-text SYNTHESIS that may fold in sensitive facts; we can't guarantee it's
        # scrubbed, so for a redacted/shared context we drop it entirely (structured facts below are filtered
        # reliably). NB: session summaries/chunks are also free text — sensitivity redaction is reliable at
        # the structured-fact level (and export), best-effort for free-text layers.
        if persona and not redact_sensitive:
            p = self.build_persona(user)
            if p:
                blocks.append(f"USER PROFILE:\n{p}")

        # WORKING MEMORY: the current session's ephemeral state ("today my throat hurts", this-trip intent)
        # — surfaced so the answer reflects "right now", but never consolidated to long-term or the profile.
        if session_id is not None:
            wm = self.working_memory(user, session_id=session_id, as_of=as_of)
            if wm:
                wl = "\n".join(f"- [{w.kind}] {w.content}"
                               for w in sorted(wm, key=lambda x: x.event_time))
                blocks.append(f"WORKING MEMORY (this session, ephemeral):\n{wl}")

        # L1 facts: hybrid retrieval per (sub-)query, unioned; + n-hop graph expansion from query entities.
        fact_map: dict[str, Fact] = {}
        for q in queries:
            for f, _ in self.retriever.retrieve(q, user, as_of, top_k or n_facts)[0]:
                fact_map.setdefault(f.id, f)
        for f in self._graph_related_facts(query, user, as_of, limit=n_facts):
            fact_map.setdefault(f.id, f)
        all_facts = list(fact_map.values())
        # Focus "mute": drop facts on topics the user asked to suppress from the read context.
        mute = self.focus.get("mute", [])
        if mute:
            all_facts = [f for f in all_facts if not self._matches(f, mute)]
        # Sensitivity redaction (feature ⑤): exclude sensitive facts when assembling a shared/export context.
        if redact_sensitive:
            all_facts = [f for f in all_facts if not getattr(f, "sensitive", False)]
        # Optional cross-encoder rerank over the FACT pool. Unlike reranking whole sessions (which the
        # cross-encoder truncates to 512 and mis-ranks — a known _S regression), facts are short, so the
        # reranker sharpens fact selection without truncation loss.
        if self.reranker is not None and len(all_facts) > (top_k or n_facts):
            order = self.reranker.rerank(query, [(str(i), f.text) for i, f in enumerate(all_facts)],
                                         top_k or n_facts)
            all_facts = [all_facts[int(i)] for i, _ in order]
        self.working_set = all_facts  # working memory: the active set for this query
        if all_facts:
            for f in all_facts:  # reinforcement-on-access: surfaced facts stay salient (spacing effect)
                reinforce(f, self.config.access_boost)
            by_date = sorted(all_facts, key=lambda f: f.valid_at, reverse=True)  # latest first (updates)
            fl = "\n".join(f"- [{fmt_date(f.valid_at)}] {f.text}" for f in by_date)
            blocks.append(f"FACTS (current, dated):\n{fl}")
            # TIMELINE: the same facts oldest->newest with explicit gaps, so 'first / most-recent / how long
            # between' is read off the order and the date arithmetic is set up for the model rather than
            # left to mental math (the temporal category's main failure mode).
            if timeline:
                chrono = sorted(all_facts, key=lambda f: f.valid_at)
                tl = "\n".join(f"- {fmt_date(f.valid_at)}: {f.text}" for f in chrono)
                blocks.append(f"TIMELINE (oldest to newest — use for ordering and durations):\n{tl}")

        # L2 coarse: retrieve session summaries per (sub-)query, ranked. This is the coarse layer of a
        # coarse-to-fine cascade (CLAUDE.md Bet E / OpenViking): summaries are tiny, so we can index and
        # rank MANY sessions cheaply — the key to scaling past a model's window (the _M / 10M frontier).
        summ_ranked: list[Episode] = []
        seen_s: set[str] = set()
        for q in queries:
            for e in self.retrieve_summaries(q, user, n_summaries):
                if e.id not in seen_s:
                    seen_s.add(e.id)
                    summ_ranked.append(e)

        # FINE drill: in cascade mode the detail chunks are the TOP-ranked summaries' own sessions (score
        # propagates coarse->fine), so we never embed-scan raw turns of irrelevant sessions. Without
        # cascade, detail is a direct episode lookup (fine for small histories).
        if cascade and summ_ranked:
            detail_eps = summ_ranked[:n_chunks] if n_chunks else []
        else:
            detail_eps = self.retrieve_episodes(query, user, n_chunks) if n_chunks else []
        detail_ids = {e.id for e in detail_eps}
        summaries = [e for e in summ_ranked if e.id not in detail_ids]
        if summaries:
            chrono = sorted(summaries, key=lambda e: e.event_time)
            sm = "\n".join(
                f"- [{e.metadata.get('date') or fmt_date(e.event_time)}] {e.summary}" for e in chrono
            )
            blocks.append(f"SESSION SUMMARIES (relevant, chronological):\n{sm}")

        if detail_eps:
            chunks = "\n\n".join(
                f"[{e.metadata.get('date') or fmt_date(e.event_time)}]\n{e.content}" for e in detail_eps
            )
            blocks.append(f"RELEVANT CONVERSATIONS (full detail):\n{chunks}")

        return "\n\n".join(blocks)[:char_budget]

    # --- read path ---
    def search(
        self,
        query: str,
        user_id: str = "default",
        as_of: Optional[float] = None,
        top_k: Optional[int] = None,
    ) -> SearchResult:
        user = self.resolver.resolve(user_id)

        # 1. multi-hop planner first (fires only for genuine >=2-hop relational questions)
        plan = self.planner.plan(query, user, as_of)
        if plan is not None:
            for f in plan.facts:
                reinforce(f, self.config.access_boost)
            return SearchResult(query=query, facts=plan.facts, via="multi-hop", _answer=plan.answer)

        # 2. hybrid retrieval
        ranked, diag = self.retriever.retrieve(query, user, as_of, top_k)
        if not ranked:
            return SearchResult(query=query, via="abstain", abstained=True)

        facts = [f for f, _ in ranked]
        scores = [s for _, s in ranked]
        if self._should_abstain(query, facts, diag):
            return SearchResult(query=query, facts=facts, scores=scores, via="abstain", abstained=True)

        reinforce(facts[0], self.config.access_boost)
        return SearchResult(query=query, facts=facts, scores=scores, via="hybrid")

    def as_of(self, query: str, when: float, user_id: str = "default", top_k: Optional[int] = None) -> SearchResult:
        """Answer 'what did we believe at time `when`?' -- bi-temporal point-in-time query."""
        return self.search(query, user_id=user_id, as_of=when, top_k=top_k)

    def retrieve_episodes(self, query: str, user_id: str = "default", k: int = 5, pool: Optional[int] = None):
        """Retrieve the top-k raw episodes (sessions) for a query: bi-encoder candidate pool → BM25
        lexical rerank (RRF) → optional cross-encoder rerank.

        BM25 layer: when pool >= total episodes (LongMemEval_S: ~54 sessions, pool up to 100), all
        episodes are in candidates and the embedding rank alone misses exact-term matches (names, places,
        dates). RRF with BM25 lifts those without replacing semantic signal. Improves preference and
        exact-entity questions where the raw text terms outperform the embedding similarity.
        """
        user = self.resolver.resolve(user_id)
        pool = pool or max(k * 5, 25)
        candidates = self.episodes_vec.search(
            self.embedder.embed(query), pool, where=lambda e: e.user_id == user
        )
        eps = [ep for _, ep in candidates]

        # BM25 + embedding RRF when we have more candidates than we'll return.
        if len(eps) > k:
            bm25 = bm25_scores(query, [(ep.id, ep.content) for ep in eps])
            if bm25:
                bm25_rank = {eid: r for r, (eid, _) in
                             enumerate(sorted(bm25.items(), key=lambda x: x[1], reverse=True))}
                K_RRF = 60  # standard RRF constant — insensitive to value in [30, 100]
                fused_order = sorted(range(len(eps)), key=lambda i: -(
                    1.0 / (K_RRF + i + 1) +  # embedding rank contribution
                    1.0 / (K_RRF + bm25_rank.get(eps[i].id, len(eps)) + 1)  # BM25 rank contribution
                ))
                eps = [eps[i] for i in fused_order]

        if self.reranker is not None and len(eps) > k:
            ranked = self.reranker.rerank(query, [(i, ep.content) for i, ep in enumerate(eps)], k)
            return [eps[i] for i, _ in ranked]
        return eps[:k]

    def context_for(
        self,
        query: str,
        user_id: str = "default",
        as_of: Optional[float] = None,
        top_k: Optional[int] = None,
        k_chunks: int = 3,
        agentic: bool = False,
        timeline: bool = False,
        hyde: bool = False,
        graph: bool = False,
        wiki: bool = False,
        summary: bool = False,
        verify: bool = False,
        intent: bool = False,
    ) -> str:
        """Assemble the hybrid read context (CLAUDE.md §3) for an LLM to answer from: live, date-stamped
        facts (conflict-resolved/current state) + the top-k raw session chunks (detail extraction drops).
        Date-stamping every line is what makes temporal + knowledge-update questions answerable.

        agentic=True swaps single-shot chunk retrieval for LLM-decomposed iterative retrieval (Bet B)."""
        user = self.resolver.resolve(user_id)

        # HyDE: expand the query with an LLM-written hypothetical answer to lift retrieval recall (M2c).
        search_query = query
        if hyde and self.llm is not None:
            hypo = self.llm.complete(
                f"Write a brief, plausible hypothetical answer (1-2 sentences) to this question, to aid "
                f"retrieval:\n{query}",
                system="You write a short plausible answer. Be concise; no preamble.",
            )
            if hypo.strip():
                search_query = f"{query}\n{hypo.strip()}"

        ranked, _ = self.retriever.retrieve(search_query, user, as_of, top_k)
        # Sort most-recent first: for knowledge-update questions the LLM should see the latest
        # fact (e.g., new job, new city) at the top — and trust it over older facts lower in the list.
        ranked_by_date = sorted(ranked, key=lambda x: x[0].valid_at, reverse=True)
        fact_lines = [f"- [{fmt_date(f.valid_at)}] {f.text}" for f, _ in ranked_by_date]
        facts_block = "\n".join(fact_lines) or "(none)"

        chunk_block = ""
        if k_chunks:
            if agentic and self.llm is not None:
                from .retrieve.agentic import AgenticRetriever

                episodes = AgenticRetriever(self, self.llm).gather_episodes(query, user, k_chunks)
            else:
                episodes = self.retrieve_episodes(search_query, user, k_chunks)
            parts = []
            for ep in episodes:
                date = ep.metadata.get("date") or fmt_date(ep.event_time)
                parts.append(f"[{date}]\n{ep.content}")
            chunk_block = "\n\n".join(parts)

        result = (
            f"FACTS (current, with dates):\n{facts_block}\n\n"
            f"RELEVANT CONVERSATIONS (with dates):\n{chunk_block}"
        ).strip()
        if timeline:
            # explicit chronological ordering of the relevant facts — helps "first/after/how long" (M2b)
            ordered = sorted((f for f, _ in ranked), key=lambda f: f.valid_at)
            tl = "\n".join(f"- [{fmt_date(f.valid_at)}] {f.text}" for f in ordered) or "(none)"
            result = f"TIMELINE (oldest to newest):\n{tl}\n\n" + result
        if graph:
            # L2: traverse the entity graph from the query's anchor entities to pull connected facts
            # across sessions (multi-hop / multi-session).
            related = self._graph_related_facts(search_query, user, as_of)
            if related:
                block = "\n".join(f"- [{fmt_date(f.valid_at)}] {f.text}" for f in related)
                result += f"\n\nRELATED FACTS (graph traversal):\n{block}"
        if wiki:
            # L4: LLM-curated per-entity notes (current vs past), synthesized at query time.
            notes = self._entity_notes(search_query, user, as_of)
            if notes:
                result = "ENTITY NOTES:\n" + "\n".join(f"- {n}" for n in notes) + "\n\n" + result
        if verify and self.llm is not None:
            # self-verify: draft an answer, find the single most useful gap, retrieve it, append evidence.
            extra = self._self_verify(query, result, user, as_of)
            if extra:
                result += f"\n\nADDITIONAL EVIDENCE (self-verify):\n{extra}"
        if summary and self.llm is not None:
            # L5: synthesize the relevant material into a short faithful summary, prepended.
            syn = self.llm.complete(
                f"Synthesize, in 2-3 faithful sentences, the facts relevant to: {query}\n\n{result}",
                system="You write a concise, strictly faithful synthesis of the given context.",
            )
            if syn.strip():
                result = f"SUMMARY:\n{syn.strip()}\n\n" + result
        if intent and self.llm is not None:
            # L6: forward-looking intent hint. Honest note: not expected to help QA benchmarks; flagged
            # for completeness and ablation.
            hint = self.llm.complete(
                f"In one short phrase, what is the user likely really trying to find out with: {query}",
                system="Reply with a short phrase only.",
            )
            if hint.strip():
                result = f"LIKELY INTENT: {hint.strip()}\n\n" + result
        return result

    def _self_verify(self, query: str, context: str, user: str, as_of: Optional[float]) -> str:
        draft = self.llm.complete(
            f"Using only this context, answer concisely. If something is missing, say what.\n\n{context}\n\nQ: {query}",
            system="Answer from context; note any missing piece.",
        )
        gap = self.llm.complete(
            f"Question: {query}\nDraft answer: {draft}\nWhat ONE short search query would best fill a gap or "
            f"verify this? Reply with the query, or 'none'.",
            system="Reply with one short search query, or exactly 'none'.",
        )
        g = gap.strip().strip(".").lower()
        if not g or g == "none":
            return ""
        more = self.retrieve_episodes(gap.strip(), user, 2)
        return "\n\n".join(f"[{ep.metadata.get('date', '?')}]\n{ep.content}" for ep in more)

    def _graph_related_facts(self, query: str, user: str, as_of: Optional[float], limit: int = 8) -> list[Fact]:
        seen: dict[str, Fact] = {}
        for eid in self.retriever.query_entity_ids(query, user):
            for direction in ("out", "in"):
                for rel in self.graph.neighbors(eid, as_of, direction):
                    f = self.fact_store.get(rel.fact_id)
                    if f is not None and f.is_live(as_of):
                        seen[f.id] = f
        return list(seen.values())[:limit]

    def _entity_notes(self, query: str, user: str, as_of: Optional[float], max_entities: int = 3) -> list[str]:
        if self.llm is None:
            return []
        notes: list[str] = []
        for eid in list(self.retriever.query_entity_ids(query, user))[:max_entities]:
            ent = self.graph.entities.get(eid)
            if ent is None:
                continue
            facts = [
                f for f in self.fact_store.values()
                if f.user_id == user and f.subject.lower() == ent.name.lower()
            ]
            if not facts:
                continue
            lines = "\n".join(
                f"[{fmt_date(f.valid_at)}] {f.text}" + ("" if f.is_live(as_of) else " (past)")
                for f in sorted(facts, key=lambda x: x.valid_at)
            )
            note = self.llm.complete(
                f"Summarize what is known about {ent.name} in 2-3 sentences. Note current vs outdated "
                f"facts.\n{lines}",
                system="You write a concise, accurate entity note that resolves current vs past facts.",
            )
            if note.strip():
                notes.append(f"{ent.name}: {note.strip()}")
        return notes

    def history(self, subject: str, predicate: str, user_id: str = "default") -> list[Fact]:
        user = self.resolver.resolve(user_id)
        return history(self.fact_store.values(), user, subject, predicate)

    def profile(self, user_id: str = "default") -> dict[str, str]:
        return self.engine.profile(self.resolver.resolve(user_id))

    # --- internals ---
    def _should_abstain(self, query: str, facts: list[Fact], diag: dict) -> bool:
        """Abstain when the query's *attribute* isn't in memory -- crucially, matching the entity name
        alone is NOT enough ("Gina's favorite food" when we only know where Gina works). We require
        lexical overlap on the predicate+object, or strong semantic similarity. Targets LongMemEval
        abstention (the false-premise category)."""

        def attribute_text(f: Fact) -> str:
            return f.predicate.replace("_", " ") + " " + f.object

        for f in facts:
            if overlap_terms(query, attribute_text(f)) - _GENERIC_ATTR_TERMS:
                return False  # a non-generic attribute term matched -> the answer is in memory
        best_sem = max(diag.get("sem", {}).values(), default=0.0)
        return best_sem < self.config.abstain_threshold
