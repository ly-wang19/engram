"""The public facade. Wires System-1 ingest, System-2 consolidation, and the hybrid + multi-hop read
path behind a small API: add() / consolidate() / search() / as_of() / history() / profile().

Defaults are fully offline (hashing embedder, rule extractor, in-memory stores) so `Memory()` works with
zero setup. Pass a real `embedder` / `llm` / store factories to run on benchmark backends.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from .config import Config
from .consolidate import ConsolidationEngine, is_single_valued, reinforce
from .consolidate.llm_extractor import EXTRACT_SYSTEM
from .consolidate.summarizer import (
    PERSONA_SYSTEM,
    SESSION_SUMMARY_SYSTEM,
    ProfileBuilder,
    SessionSummarizer,
)
from .embed import Embedder, HashingEmbedder
from .ingest import IdentityResolver, Ingestor
from .llm import LLM
from .retrieve import (
    HybridRetriever,
    MultiHopPlanner,
    extract_aggregation_candidates,
    history,
    plan_evidence,
    render_aggregation_candidates,
)
from .retrieve.lexical import bm25_scores, overlap_terms, stems
from .store import (
    GraphStore,
    InMemoryDocStore,
    InMemoryGraphStore,
    InMemoryVectorStore,
    VectorStore,
    load_memory,
    save_memory,
)
from .types import Conflict, Episode, Fact, WorkingMemory
from .util import DAY, fmt_date, now

# The editable memory-policy prompts (the console's "提示词" / "要记录什么记忆"). Defaults are the
# built-in prompts; a per-user override (empty string = use default) is stored in Memory.policy.
POLICY_DEFAULTS = {
    "extract_instruction": "",  # additive directive: what to record / what to ignore
    "extract_system": EXTRACT_SYSTEM,
    "summary_system": SESSION_SUMMARY_SYSTEM,
    "persona_system": PERSONA_SYSTEM,
}

# words too generic to confirm an attribute on their own ("favorite food" must not match
# "favorite programming language" just because both contain "favorite").
_GENERIC_ATTR_TERMS = {"favorite", "favourite", "name", "is", "are", "of", "the"}

# Answer-TYPE alignment (#2/#3): when a question demands a STRUCTURED value (an id, a date, a number, an
# email, a phone, a url), the answer's object must actually look like that type — otherwise a high semantic
# match is spurious (e.g. "what's the project ID?" retrieving the project OWNER's name). We then surface a
# type-matching fact, or abstain, instead of confidently returning a type-mismatched top fact. Cues are
# deliberately strong (id/编号, not bare "when") to avoid false abstains on free-text answers.
_ANSWER_TYPE_CUES = {
    "email": ("email", "e-mail", "邮箱", "邮件地址"),
    "url": ("url", "链接", "网址", "link to"),
    "phone": ("phone number", "telephone", "电话", "手机号", "联系电话", "phone"),
    "id": ("id", "编号", "工单号", "订单号", "identifier", "order number", "ticket number", "serial"),
    "date": ("what date", "which day", "date of", "日期", "什么时候", "哪天", "哪一天", "几号", "哪一年"),
    "number": ("how many", "how much", "number of", "多少", "几个", "数量", "几次", "几年", "几岁"),
}
_ANSWER_TYPE_MATCH = {
    "email": lambda o: bool(re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", o)),
    "url": lambda o: bool(re.search(r"https?://|www\.", o)),
    "phone": lambda o: bool(re.search(r"\+?\d[\d\s().\-]{6,}\d", o)),
    # an id is an alnum code containing a digit, no spaces (PROJ-1024, A12B); a plain name has none of that
    "id": lambda o: bool(re.search(r"\d", o)) and len(o.strip()) <= 40 and " " not in o.strip(),
    "date": lambda o: bool(re.search(r"\b\d{4}\b|\d{1,2}[-/]\d{1,2}|年|月|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec", o.lower())),
    "number": lambda o: bool(re.search(r"\d", o)),  # lenient: any digit (counts/durations/ages)
}
_TEMPORAL_HISTORY_RE = re.compile(
    r"\b(before|previous|previously|former|formerly|used\s+to|past|old(?:er)?|prior)\b|"
    r"以前|之前|曾经|过去|原来|从前|上一",
    re.IGNORECASE,
)
_PROCEDURAL_QUERY_RE = re.compile(
    r"\b(how\s+(?:do|should|can|to|would|am|is)|procedure|process|workflow|runbook|instruction|"
    r"rule|policy|protocol|steps?|checklist|always|never|remind|remember\s+to)\b|"
    r"怎么|如何|步骤|流程|规则|指令|操作|办法|提醒|记得",
    re.IGNORECASE,
)


def _expected_answer_type(query: str):
    q = query.lower()
    for t, cues in _ANSWER_TYPE_CUES.items():
        for c in cues:
            # word-boundary match for ASCII cues so "id" doesn't fire on "did"/"said"; substring for CJK
            if c.isascii():
                if re.search(r"\b" + re.escape(c) + r"\b", q):
                    return t
            elif c in q:
                return t
    return None


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

        custom_vector_factory = vector_store_factory is not InMemoryVectorStore
        if self.config.storage == "lancedb" and not custom_vector_factory:
            from .store.lancedb_store import LanceDBVectorStore

            base = self.config.data_path or tempfile.mkdtemp(prefix="engram_lancedb_")

            def make_vector_store(name: str) -> VectorStore:
                return LanceDBVectorStore(base, name)
        else:
            def make_vector_store(name: str) -> VectorStore:
                return vector_store_factory()

        self.episodes_doc = InMemoryDocStore()
        self.episodes_vec = make_vector_store("episodes_vec")
        self.fact_store = make_vector_store("fact_store")  # HOT tier: the fast, frequently-retrieved working set
        self.cold_store = make_vector_store("cold_store")  # COLD tier: aged-out facts, preserved (never deleted)
        self.summary_vec = make_vector_store("summary_vec")  # L2 session summaries, retrievable for a lean read slice
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
        # Resolved user identity (user_id -> self-name, e.g. "user123" -> "李雷"). Persisted so the
        # first-person normalization and the profile know who the user is after a reload.
        self._identity: dict[str, str] = {}
        self._aliases: dict[str, set] = {}  # user_id -> {all declared names/nicknames} (coreference)
        # Content fingerprints of already-imported episodes (idempotent re-import: the same export twice
        # must not double every memory). Persisted with the snapshot (manifest state).
        self._import_seen: set[str] = set()
        # Suspected conflicts surfaced for the user to confirm (System-2 LLM detection; never auto-applied).
        self.conflicts: dict[str, Conflict] = {}
        self.cold_pages_out: dict[str, int] = {}
        self.cold_pages_in: dict[str, int] = {}
        self._persist_path: Optional[str] = None
        self._rewire()

    def _all_facts(self) -> list[Fact]:
        return self.fact_store.values() + self.cold_store.values()

    def _upsert_fact(self, fact: Fact, *, tier: str = "existing") -> None:
        """Write a mutated fact back to the tier that owns it, or force-promote it to hot.

        In-memory stores hold object references, but real vector backends often copy payloads on upsert/get.
        Mutating a returned Fact is therefore not enough; every semantic edit/invalidation must explicitly
        write the payload back to exactly one tier.
        """
        in_cold = self.cold_store.get(fact.id) is not None
        in_hot = self.fact_store.get(fact.id) is not None
        if tier == "hot" or (tier == "existing" and not in_cold):
            self.cold_store.delete(fact.id)
            self.fact_store.upsert(fact.id, fact.embedding or [], fact)
            return
        if in_hot:
            self.fact_store.delete(fact.id)
        self.cold_store.upsert(fact.id, fact.embedding or [], fact)

    def _rewire(self) -> None:
        """(Re)bind the pipeline components to the current stores. Called at init and after loading a
        snapshot, so persistence can swap the stores in without touching the embedder/llm (which aren't
        serialized)."""
        self.ingestor = Ingestor(self.episodes_doc, self.episodes_vec, self.embedder, self.resolver)
        self.engine = ConsolidationEngine(self.fact_store, self.graph, self.embedder, self.config, self.llm)
        self.retriever = HybridRetriever(self.fact_store, self.graph, self.embedder, self.config)
        self.cold_retriever = HybridRetriever(self.cold_store, self.graph, self.embedder, self.config)
        self.planner = MultiHopPlanner(
            self.graph,
            self.fact_store,
            self.config,
            extra_fact_stores=[self.cold_store],
            llm=self.llm,
        )
        # restore the persisted self-name into the (freshly built) extractor so identity survives reload
        ex = getattr(self.engine, "extractor", None)
        if ex is not None and hasattr(ex, "self_name"):
            ex.self_name.update(getattr(self, "_identity", {}))
            if hasattr(ex, "aliases"):
                for k, v in getattr(self, "_aliases", {}).items():
                    ex.aliases.setdefault(k, set()).update(v)

    # --- persistence (so memory survives across processes/sessions; CLAUDE.md §6) ---
    def save(self, path: Optional[str] = None) -> None:
        """Snapshot durable memory state to a safe JSONL+manifest store.

        The embedder/llm are not serialized; reopen with the runtime backends you want to use. Normal
        operation intentionally does not write pickle.
        """
        path = path or self._persist_path
        if not path:
            raise ValueError("no path to save to")
        backend = "lancedb" if self.config.storage == "lancedb" else "durable"
        save_memory(self, path, backend=backend)
        self._persist_path = path

    @classmethod
    def open(cls, path: str, allow_mismatch: bool = False, **kwargs) -> "Memory":
        """Open a persistent Memory from a JSONL+manifest store, or start empty if it does not exist.

        `allow_mismatch=True` bypasses the embedding-space guards ONLY so `.reembed()` can migrate the
        store to the attached embedder — opening mismatched for any other purpose corrupts retrieval."""
        cfg = kwargs.get("config")
        if (
            cfg is not None
            and getattr(cfg, "storage", None) == "lancedb"
            and getattr(cfg, "data_path", None) is None
            and "vector_store_factory" not in kwargs
        ):
            kwargs["config"] = replace(cfg, data_path=f"{path}/lancedb")
        mem = cls(**kwargs)
        if load_memory(mem, path, allow_mismatch=allow_mismatch):
            mem._rewire()
            mem._classify()  # backfill category/sensitivity on facts saved before feature ⑤
        mem._persist_path = path
        return mem

    def reembed(self, embedder: Optional[Embedder] = None) -> dict[str, int]:
        """Migrate every stored vector into `embedder`'s space (default: the currently attached one) —
        the supported path for switching embedding models over an existing store (the manifest guards
        refuse a mismatched open; this is the escape hatch they point to). Re-embeds facts (hot + cold),
        episode contents, session summaries, and working memory from their source TEXT. Entity nodes are
        skipped: retrieval matches entities by name, not embedding. Call `.save()` afterwards — the
        manifest then records the new embedder identity, so the next open is clean."""
        if embedder is not None:
            self.embedder = embedder

        def _clear(store) -> None:
            for payload in list(store.values()):
                key = getattr(payload, "id", None)
                if key is not None:
                    store.delete(key)

        counts: dict[str, int] = {}
        for name, store in (("facts", self.fact_store), ("cold", self.cold_store)):
            facts = store.values()
            _clear(store)
            vecs = self.embedder.embed_batch([f.text for f in facts]) if facts else []
            for f, v in zip(facts, vecs):
                f.embedding = v
                store.upsert(f.id, v, f)
            counts[name] = len(facts)

        episodes = list(self.episodes_doc.values())  # the doc log is the source of truth for episode text
        _clear(self.episodes_vec)
        vecs = self.embedder.embed_batch([ep.content for ep in episodes]) if episodes else []
        for ep, v in zip(episodes, vecs):
            ep.embedding = v
            self.episodes_vec.upsert(ep.id, v, ep)
        counts["episodes"] = len(episodes)

        summarized = [ep for ep in episodes if ep.summary]
        _clear(self.summary_vec)
        # same embedding source as summarize_episodes: the summary, falling back to a content excerpt
        vecs = (self.embedder.embed_batch([ep.summary or ep.content[:200] for ep in summarized])
                if summarized else [])
        for ep, v in zip(summarized, vecs):
            ep.summary_embedding = v
            self.summary_vec.upsert(ep.id, v, ep)
        counts["summaries"] = len(summarized)

        for wm in self.working_mem.values():
            wm.embedding = self.embedder.embed(wm.content)
        counts["working"] = len(self.working_mem)

        self._persona_cache.clear()
        self._rewire()  # the retriever/engine hold embedder refs (and the semantic gate) — rebind
        return counts

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
        ex = getattr(self.engine, "extractor", None)  # capture any newly-resolved identity to persist it
        if ex is not None and hasattr(ex, "self_name"):
            self._identity.update(ex.self_name)
            for k, v in getattr(ex, "aliases", {}).items():
                self._aliases.setdefault(k, set()).update(v)
        self._classify()  # feature ⑤: tag new facts with a category + sensitivity flag (rule-based)
        self._detect_conflicts()  # System-2: surface suspected conflicts for the user (opt-in, gated)
        self._enforce_hot_limit()
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

    # --- batch import (CLAUDE.md §6 adoption layer): bring your own history in bulk ---
    def import_messages(
        self,
        sessions,
        user_id: str = "default",
        consolidate: bool = True,
        summarize: bool = True,
        roles: bool = True,
        batch_size: int = 256,
        base_time: Optional[float] = None,
        dedupe: bool = True,
    ) -> dict[str, int]:
        """Bulk-ingest pre-parsed sessions (from `engram.connectors.parse`) as ONE episode per session,
        batch-embedding the bodies in a few encode calls and then running ONE System-2 pass over just
        the new episodes (extract facts + build graph, optionally L2 summaries). This is the efficient
        path for importing a whole chat history — far cheaper than `add()` per turn (one model.encode of
        N sessions instead of N).

        `sessions` is an iterable of `ImportSession` (or dicts shaped `{session_id, messages, ...}`).
        `base_time` supplies a synthetic clock (base + i·day) for sessions the source didn't timestamp,
        so chronological order is preserved even without dates. Returns ingest + consolidation stats.

        `dedupe` (default on) makes re-imports idempotent: an episode whose content fingerprint was
        already ingested is skipped (counted in `skipped`) — re-posting the same export no longer doubles
        every memory. Fingerprints are content+identity (never time: re-imports get fresh synthetic
        clocks). `dedupe=False` restores raw append behavior.
        """
        import hashlib

        from .connectors.base import ImportMessage, ImportSession, to_epoch

        def _time_of(d: dict) -> Optional[float]:
            for key in ("event_time", "timestamp", "time", "created_at", "create_time", "date", "ts"):
                if key in d and d[key] not in (None, ""):
                    return to_epoch(d[key])
            return None

        def _coerce(s) -> Optional[ImportSession]:
            if isinstance(s, ImportSession):
                return s
            if isinstance(s, dict) and "messages" in s:
                msgs = [ImportMessage(content=str(m.get("content", "")),
                                      speaker=str(m.get("speaker") or m.get("role") or "user"),
                                      event_time=_time_of(m),
                                      metadata=dict(m.get("metadata", {})))
                        for m in s["messages"] if isinstance(m, dict)]
                metadata = dict(s.get("metadata", {}))
                metadata.setdefault("source", "sessions")
                return ImportSession(session_id=str(s.get("session_id", "imported")), messages=msgs,
                                     event_time=_time_of(s), title=str(s.get("title", "")),
                                     metadata=metadata)
            return None

        items = [c for c in (_coerce(s) for s in sessions) if c is not None]
        base = base_time if base_time is not None else now()
        texts: list[str] = []
        metas: list[tuple] = []
        session_count = 0
        skipped = 0

        def _fp(sid: str, row_key: str) -> str:
            return hashlib.sha1(f"{user_id}|{sid}|{row_key}".encode()).hexdigest()
        for i, s in enumerate(items):
            source = str(s.metadata.get("source", ""))
            dated_times = {m.event_time for m in s.messages if m.event_time is not None}
            split_messages = source in {"records", "sessions"} and len(dated_times) > 1
            if split_messages:
                added = 0
                fallback = s.start_time()
                for j, m in enumerate(s.messages):
                    text = (m.content or "").strip()
                    if not text:
                        continue
                    body = f"{m.speaker}: {text}" if roles else text
                    sid = s.session_id or f"imported_{i}"
                    # per-row fingerprint keeps the row index, so a legitimately repeated message within
                    # one session is NOT treated as a duplicate — only re-imports collide.
                    fp = _fp(sid, f"{j}|{body}")
                    if dedupe and fp in self._import_seen:
                        skipped += 1
                        continue
                    et = m.event_time if m.event_time is not None else fallback
                    et = et if et is not None else base + i * DAY + j
                    texts.append(body)
                    metas.append((sid, et, s.title, fp))
                    added += 1
                if added:
                    session_count += 1
                continue

            body = s.to_text(roles=roles)
            if not body.strip():
                continue
            sid = s.session_id or f"imported_{i}"
            fp = _fp(sid, body)
            if dedupe and fp in self._import_seen:
                skipped += 1
                continue
            et = s.start_time()
            et = et if et is not None else base + i * DAY  # synthetic but ordered
            texts.append(body)
            metas.append((sid, et, s.title, fp))
            session_count += 1
        if not texts:
            return {"sessions": 0, "episodes": 0, "facts_added": 0, "summaries": 0, "skipped": skipped}

        vecs: list = []
        for j in range(0, len(texts), batch_size):
            vecs.extend(self.embedder.embed_batch(texts[j:j + batch_size]))

        new_eps: list[Episode] = []
        for (sid, et, title, fp), text, vec in zip(metas, texts, vecs):
            ep = self.add(text, user_id=user_id, session_id=sid, speaker="session",
                          event_time=et, embedding=vec)
            ep.metadata["date"] = fmt_date(et)
            if title:
                ep.metadata["title"] = title
            new_eps.append(ep)
            self._import_seen.add(fp)  # only after the episode actually landed

        stats = {"sessions": session_count, "episodes": len(new_eps), "facts_added": 0, "summaries": 0,
                 "skipped": skipped}
        if consolidate:
            stats["facts_added"] = self.consolidate(new_eps).get("facts_added", 0)
        if summarize:
            stats["summaries"] = self.summarize_episodes(new_eps)
        return stats

    def import_data(self, data, format: str = "auto", user_id: str = "default",
                    session_id: str = "imported", **kwargs) -> dict[str, int]:
        """Convenience: parse a raw export (ChatGPT/OpenAI/JSONL/transcript — auto-sniffed) and import it
        in one call. See `engram.connectors.parse` for formats."""
        from .connectors import parse
        return self.import_messages(parse(data, format=format, session_id=session_id),
                                    user_id=user_id, **kwargs)

    def import_snapshot(self, snapshot: dict, user_id: str = "default", dedupe: bool = True) -> dict:
        """Restore an Engram export snapshot (`/v1/export` / `MemoryService.export`) — the portability
        round-trip. A snapshot is ALREADY-CONSOLIDATED memory, so this is a restore, not an ingest:
        facts land directly with their bi-temporal stamps and supersession chains intact (re-running
        extraction would duplicate them and flatten history), and episodes re-enter the dated raw log
        marked consolidated so System-2 never re-digests them. The share-safe export has no episodes —
        restoring facts alone is the expected outcome there. Idempotent under `dedupe` (default)."""
        from .connectors.base import to_epoch

        user = self.resolver.resolve(user_id)

        # --- episodes: restore the lossless dated log (present only in include_sensitive exports) ---
        ep_seen = {(ep.session_id, ep.content) for ep in self.episodes_doc.values()
                   if ep.user_id == user}
        eps_restored = eps_skipped = 0
        for row in snapshot.get("episodes") or []:
            if not isinstance(row, dict):
                continue
            content = str(row.get("content") or "")
            if not content:
                continue
            session_id = str(row.get("session_id") or "imported")
            if dedupe and (session_id, content) in ep_seen:
                eps_skipped += 1
                continue
            ep = Episode(content=content, user_id=user, session_id=session_id,
                         speaker=str(row.get("speaker") or "user"),
                         event_time=to_epoch(row.get("event_time")) or now(),
                         consolidated=True,  # its facts arrive below; never re-extract
                         summary=str(row.get("summary") or ""))
            ep.embedding = self.embedder.embed(content)
            if ep.summary:
                ep.summary_embedding = self.embedder.embed(ep.summary)
            self.episodes_doc.put(ep.id, ep)
            self.episodes_vec.upsert(ep.id, ep.embedding, ep)
            ep_seen.add((session_id, content))
            eps_restored += 1

        # --- facts: restore both live and superseded rows, preserving valid/transaction time ---
        fact_seen = {(f.subject, f.predicate, f.object, round(f.valid_at, 1))
                     for f in self._all_facts() if f.user_id == user}
        id_map: dict[str, str] = {}  # old snapshot id -> new id, to re-link supersession chains
        staged: list[tuple[Fact, str]] = []
        facts_skipped = malformed = 0
        for row in snapshot.get("facts") or []:
            if not isinstance(row, dict):
                malformed += 1
                continue
            s = str(row.get("subject") or "").strip()
            p = str(row.get("predicate") or "").strip()
            o = str(row.get("object") or "").strip()
            if not (s and p and o):
                malformed += 1
                continue
            valid_at = to_epoch(row.get("valid_at")) or now()
            key = (s, p, o, round(valid_at, 1))
            if dedupe and key in fact_seen:
                facts_skipped += 1
                continue
            try:
                salience = float(row.get("salience", 1.0))
                confidence = float(row.get("confidence", 1.0))
            except (TypeError, ValueError):
                salience, confidence = 1.0, 1.0
            f = Fact(subject=s, predicate=p, object=o, user_id=user,
                     text=str(row.get("text") or ""),
                     source=str(row.get("source") or "extracted"),
                     category=str(row.get("category") or ""),
                     sensitive=bool(row.get("sensitive", False)),
                     salience=salience, confidence=confidence,
                     valid_at=valid_at,
                     invalid_at=to_epoch(row.get("invalid_at")),
                     created_at=to_epoch(row.get("created_at")) or now(),
                     expired_at=to_epoch(row.get("expired_at")),
                     # source-instance episode ids: kept as-is for audit honesty — the referenced
                     # episodes got fresh ids here, so these name where the fact CAME from.
                     provenance=[str(x) for x in (row.get("provenance") or [])])
            f.embedding = self.embedder.embed(f.text)
            old_id = str(row.get("id") or "")
            if old_id:
                id_map[old_id] = f.id
            staged.append((f, str(row.get("supersedes") or "")))
            fact_seen.add(key)
        # Second pass so chains resolve regardless of row order; a supersedes target outside the
        # snapshot (or skipped as a duplicate) becomes None rather than a dangling foreign id.
        for f, old_sup in staged:
            f.supersedes = id_map.get(old_sup) if old_sup else None
            self.fact_store.upsert(f.id, f.embedding, f)
            # add_fact carries fact.invalid_at onto the relation, so superseded history lands as
            # already-invalidated edges — the graph's timeline survives the round-trip.
            self.engine.graph_builder.add_fact(f)

        self._enforce_hot_limit()
        self._persona_cache.clear()
        return {"format": "engram", "facts_restored": len(staged), "facts_skipped": facts_skipped,
                "episodes_restored": eps_restored, "episodes_skipped": eps_skipped,
                "malformed": malformed}

    def link_identity(self, a: str, b: str) -> str:
        return self.resolver.link(a, b)

    # --- user-authored memory management (the editable layer the management UI drives) ---
    def add_fact(self, subject: str, predicate: str, object: str, user_id: str = "default",
                 valid_at: Optional[float] = None, sensitive: Optional[bool] = None,
                 category: Optional[str] = None) -> Fact:
        """Manually assert a fact. It is marked source='user' (authoritative): conflict resolution lets it
        supersede any extracted value on the same slot, and it is then protected from future auto-overrides."""
        user = self.resolver.resolve(user_id)
        f = Fact(subject=subject, predicate=predicate, object=object, user_id=user, source="user",
                 valid_at=valid_at if valid_at is not None else now())
        f.embedding = self.embedder.embed(f.text)
        from .consolidate.classify import classify_fact
        classify_fact(f)  # tag category + sensitivity (feature ⑤)
        if category is not None:
            f.category = category
        if sensitive is not None:
            f.sensitive = sensitive
        live = [x for x in self._all_facts() if x.user_id == user and x.is_live()]
        action, invalidated = self.engine.conflict.reconcile(f, live)
        for old in invalidated:
            self.engine.graph_builder.invalidate(old.id, f.created_at)
            self._upsert_fact(old, tier="hot")
        if action != "duplicate":
            self.fact_store.upsert(f.id, f.embedding, f)
            self.engine.graph_builder.add_fact(f)
        self._enforce_hot_limit()
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
        self.graph.delete_relations_for_fact(f.id)
        self.graph.prune_orphan_entities()
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
        self._upsert_fact(f, tier="hot")
        self.engine.graph_builder.add_fact(f)
        self._persona_cache.clear()
        return f

    def delete_fact(self, fact_id: str) -> bool:
        """Right-to-forget: HARD-remove a fact (distinct from auto-invalidation, which keeps history). This
        is user-initiated erasure, so the data is actually gone — from both the hot and cold tiers."""
        existed = self.fact_store.get(fact_id) is not None or self.cold_store.get(fact_id) is not None
        self.graph.delete_relations_for_fact(fact_id)
        self.graph.prune_orphan_entities()
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

    def graph_data(
        self,
        user_id: str = "default",
        as_of: Optional[float] = None,
        include_sensitive: bool = True,
        q: str = "",
        live_only: bool = False,
        limit: Optional[int] = None,
    ) -> dict:
        """Export the semantic graph as nodes + edges for the 关系图谱 visualization. Entities are nodes;
        relations are edges carrying their predicate and bi-temporal (live/superseded) status. Orphan
        entities (no surviving edge) are dropped so the picture stays about relationships.

        `include_sensitive=False` returns a share-safe graph derived only from facts not tagged sensitive.
        """
        user = self.resolver.resolve(user_id)
        ents = {e.id: e for e in self.graph.entities.values() if e.user_id == user}
        relations = self.graph.relations()
        live_relation_fact_ids = {
            r.fact_id for r in relations
            if r.invalid_at is None
        }
        needle = q.strip().lower()
        edges, touched = [], set()
        for r in relations:
            if r.subject_id not in ents or r.object_id not in ents:
                continue
            live = (
                r.invalid_at is None
                if as_of is None
                else r.valid_at <= as_of and (r.invalid_at is None or r.invalid_at > as_of)
            )
            if live_only and not live:
                continue
            if as_of is None and not live and r.fact_id in live_relation_fact_ids:
                continue
            if as_of is not None and not live:
                continue
            fact = self.fact_store.get(r.fact_id) or self.cold_store.get(r.fact_id)
            if fact is None:
                continue
            if not include_sensitive and getattr(fact, "sensitive", False):
                continue
            subject_name = ents[r.subject_id].name
            object_name = ents[r.object_id].name
            haystack = " ".join([
                subject_name,
                object_name,
                r.predicate,
                getattr(fact, "text", ""),
                getattr(fact, "object", ""),
                getattr(fact, "category", ""),
            ]).lower()
            if needle and needle not in haystack:
                continue
            edges.append({
                "source": r.subject_id,
                "target": r.object_id,
                "predicate": r.predicate.replace("_", " "),
                "live": live,
                "fact_id": r.fact_id,
                "fact_text": fact.text if fact is not None else "",
                "valid_at": r.valid_at,
                "valid_at_h": fmt_date(r.valid_at),
                "invalid_at": r.invalid_at,
                "invalid_at_h": fmt_date(r.invalid_at) if r.invalid_at is not None else None,
                "provenance": list(fact.provenance) if fact is not None else [],
            })
            touched.add(r.subject_id)
            touched.add(r.object_id)
            if limit is not None and limit > 0 and len(edges) >= limit:
                break
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

    # --- conflict detection -> pending (LLM detects the ambiguous tail; the USER confirms) ---
    def _detect_conflicts(self) -> None:
        if not (self.llm is not None and self.config.conflict_detection):
            return  # opt-in; offline / rule-only mode stays deterministic
        from .consolidate.detect import detect_conflicts
        seen = {c.pair_key for c in self.conflicts.values()}
        for user in {f.user_id for f in self._all_facts()}:
            live = [f for f in self._all_facts() if f.user_id == user and f.is_live()]
            for c in detect_conflicts(live, self.llm, user, seen, self.embedder):
                self.conflicts[c.id] = c
                seen.add(c.pair_key)

    def pending_conflicts(self, user_id: str = "default") -> list[Conflict]:
        """Suspected conflicts awaiting the user's decision (both facts must still be live)."""
        user = self.resolver.resolve(user_id)
        out = []
        for c in self.conflicts.values():
            if c.user_id != user or c.status != "pending":
                continue
            a = self.fact_store.get(c.older) or self.cold_store.get(c.older)
            b = self.fact_store.get(c.newer) or self.cold_store.get(c.newer)
            if a is not None and b is not None and a.is_live() and b.is_live():
                out.append(c)
            else:
                c.status = "dismissed"  # one side already changed -> no longer a live conflict
        return out

    def resolve_conflict(self, conflict_id: str, keep: str = "newer") -> bool:
        """Apply the user's decision: keep='newer'|'older' supersedes the other; keep='both' just dismisses.
        This is the ONLY path that acts on a detected conflict — always user-driven."""
        c = self.conflicts.get(conflict_id)
        if c is None:
            return False
        newer = self.fact_store.get(c.newer) or self.cold_store.get(c.newer)
        older = self.fact_store.get(c.older) or self.cold_store.get(c.older)
        if keep in ("newer", "older") and newer is not None and older is not None:
            winner, loser = (newer, older) if keep == "newer" else (older, newer)
            if loser.invalid_at is None:
                loser.invalid_at = max(winner.valid_at, loser.valid_at)
            loser.expired_at = now()
            winner.supersedes = loser.id
            self.engine.graph_builder.invalidate(loser.id, loser.expired_at)
            self._upsert_fact(loser)
            self._upsert_fact(winner)
            self._persona_cache.clear()
        c.status = "resolved"
        return True

    def dismiss_conflict(self, conflict_id: str) -> bool:
        """Not a conflict (keep both) — won't be flagged again."""
        c = self.conflicts.get(conflict_id)
        if c is None:
            return False
        c.status = "dismissed"
        return True

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

    def retrieve_summaries(
        self,
        query: str,
        user_id: str = "default",
        k: int = 8,
        as_of: Optional[float] = None,
    ) -> list[Episode]:
        """Top-k session summaries for a query, via the SAME hybrid (dense + BM25, RRF) signal as fact and
        episode retrieval. Lexical matching matters here: aggregation questions ('how many trips') hinge on
        exact terms a summary mentions, which a pure-embedding lookup can rank below a vaguely-similar one.
        Returns episodes carrying the .summary field."""
        user = self.resolver.resolve(user_id)
        pool = max(k * 3, 30)
        cands = self.summary_vec.search(
            self.embedder.embed(query),
            pool,
            where=lambda e: e.user_id == user and (as_of is None or e.event_time <= as_of),
        )
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
        facts = [f for f in self._all_facts() if f.user_id == user]
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
            self.cold_pages_out[f.user_id] = self.cold_pages_out.get(f.user_id, 0) + 1
        return n_out

    def _enforce_hot_limit(self) -> int:
        max_hot = int(getattr(self.config, "max_hot_facts", 0) or 0)
        if max_hot <= 0:
            return 0
        return self.evict_cold(max_hot)

    def _page_hot(self, facts: list[Fact]) -> None:
        """Promote retrieved cold facts back into the hot index. The fact id and graph edges stay stable."""
        moved = False
        for f in facts:
            if self.cold_store.get(f.id) is not None:
                self.cold_store.delete(f.id)
                reinforce(f, self.config.access_boost)
                self.fact_store.upsert(f.id, f.embedding or [], f)
                self.cold_pages_in[f.user_id] = self.cold_pages_in.get(f.user_id, 0) + 1
                moved = True
        if moved:
            self._enforce_hot_limit()

    def _retrieve_cold(
        self,
        query: str,
        user: str,
        as_of: Optional[float],
        top_k: Optional[int],
    ) -> tuple[list[tuple[Fact, float]], dict]:
        ranked, diag = self.cold_retriever.retrieve(query, user, as_of, top_k)
        if ranked:
            self._page_hot([f for f, _ in ranked])
        return ranked, diag

    def procedural(self, user_id: str = "default", as_of: Optional[float] = None) -> list[Fact]:
        """Standing instructions / how-to facts for this user (procedural memory)."""
        user = self.resolver.resolve(user_id)
        return [f for f in self._all_facts()
                if f.user_id == user and f.is_live(as_of)
                and (f.predicate.lower() in self._INSTRUCTION_PREDS
                     or any(f.predicate.lower().startswith(p + "_") for p in ("wants", "prefers", "remind")))]

    def _is_procedural_fact(self, f: Fact) -> bool:
        pred = f.predicate.lower()
        return pred in self._INSTRUCTION_PREDS or any(
            pred.startswith(p + "_") for p in ("wants", "prefers", "remind")
        )

    def _procedural_source_label(self, f: Fact) -> str:
        sessions: list[str] = []
        seen: set[str] = set()
        for ep_id in f.provenance:
            ep = self.episodes_doc.get(ep_id)
            if ep is None or ep.session_id in seen:
                continue
            seen.add(ep.session_id)
            sessions.append(ep.session_id)
        if sessions:
            return "sessions: " + ", ".join(sessions[:3])
        return f"fact: {f.id}"

    def _procedural_candidates(
        self,
        query: str,
        user: str,
        as_of: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> list[Fact]:
        if not self.config.procedural_memory:
            return []
        k = max(1, int(limit or getattr(self.config, "procedural_memory_k", 6) or 6))
        q_is_proc = bool(_PROCEDURAL_QUERY_RE.search(query))
        rows: list[tuple[int, float, Fact]] = []
        for f in self._all_facts():
            if f.user_id != user or not f.is_live(as_of) or not self._is_procedural_fact(f):
                continue
            text = " ".join(
                part for part in (
                    f.subject,
                    f.predicate.replace("_", " "),
                    f.object,
                    f.display,
                )
                if part
            )
            exact = overlap_terms(query, text) - _GENERIC_ATTR_TERMS
            object_hits = overlap_terms(query, f.object)
            subject_hits = overlap_terms(query, f.subject)
            if not exact and not q_is_proc:
                continue
            if q_is_proc and not (exact or subject_hits or object_hits):
                continue
            score = len(exact) + (3 if object_hits else 0) + (2 if subject_hits else 0)
            if f.provenance:
                score += 1
            rows.append((score, f.valid_at, f))
        rows.sort(key=lambda row: (-row[0], -row[1], row[2].text.lower()))
        return [f for _, _, f in rows[:k]]

    def _procedural_memory_block(
        self,
        query: str,
        user: str,
        as_of: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> str:
        rows = self._procedural_candidates(query, user, as_of, limit=limit)
        if not rows:
            return ""
        lines = []
        for f in rows:
            source = self._procedural_source_label(f)
            pred = f.predicate.replace("_", " ")
            lines.append(
                f"- [{self._stamp(f)}] ({source}) {f.subject} {pred}: {f.object}"
            )
        return "PROCEDURAL MEMORY (standing rules/how-to, source-backed):\n" + "\n".join(lines)

    def structured_profile(self, user_id: str = "default") -> dict:
        """L2 structured profile: the user's live facts grouped into basic info / preferences / habits,
        split into confirmed vs tentative for DISPLAY. This is a read-only derived view — it never filters
        the fact store or the retrieval path, so recall is unaffected (search/lean_context see all facts)."""
        from .consolidate.structured import build_structured_profile
        user = self.resolver.resolve(user_id)
        subject = self.engine.self_name(user)
        live = [f for f in self._all_facts() if f.user_id == user and f.is_live()]
        return build_structured_profile(live, subject, user)

    def build_persona(self, user_id: str = "default") -> str:
        """L3: a compact narrative profile (preferences/habits/possessions) synthesized from live facts."""
        user = self.resolver.resolve(user_id)
        if user in self._persona_cache:
            return self._persona_cache[user]
        subject = self.engine.self_name(user)
        mute = self.focus.get("mute", [])
        live = [f for f in self._all_facts()
                if f.user_id == user and f.is_live() and not self._matches(f, mute)]
        persona = (self.profiles.narrative(subject, live, llm=self.llm,
                                           system=self._effective("persona_system")) if live else "")
        track = self.focus.get("track", [])
        if track:  # surface the user's declared priorities in their profile
            line = "FOCUS AREAS (user asked to prioritize): " + ", ".join(track)
            persona = (persona + "\n" + line).strip() if persona else line
        self._persona_cache[user] = persona
        return persona

    def _persona_at(self, user: str, as_of: Optional[float]) -> str:
        """Narrative profile for a read context. Current profiles are cached; as-of profiles are not,
        because the timestamp is part of the view and must not leak current facts into past contexts."""
        if as_of is None:
            return self.build_persona(user)
        subject = self.engine.self_name(user)
        mute = self.focus.get("mute", [])
        live = [
            f for f in self._all_facts()
            if f.user_id == user and f.is_live(as_of) and not self._matches(f, mute)
        ]
        return self.profiles.narrative(
            subject,
            live,
            llm=self.llm,
            system=self._effective("persona_system"),
        ) if live else ""

    def _current_state_block(self, facts: list[Fact], limit: int = 18) -> str:
        """Latest live value per single-valued slot, plus all live multi-valued state rows."""
        latest: dict[tuple[str, str], Fact] = {}
        multi: list[Fact] = []
        for f in facts:
            if not is_single_valued(f.predicate):
                multi.append(f)
                continue
            key = (f.subject.lower(), f.predicate.lower())
            if key not in latest or f.valid_at > latest[key].valid_at:
                latest[key] = f
        rows = sorted(
            list(latest.values()) + multi,
            key=lambda f: (f.subject.lower(), f.predicate.lower(), f.object.lower(), -f.valid_at),
        )[:limit]
        if not rows:
            return ""
        lines = ["date | subject | attribute | current value", "--- | --- | --- | ---"]
        for f in rows:
            lines.append(f"{fmt_date(f.valid_at)} | {f.subject} | {f.predicate.replace('_', ' ')} | {f.object}")
        return "CURRENT STATE (live facts only):\n" + "\n".join(lines)

    _PREFERENCE_MARKERS = (
        "like", "dislike", "prefer", "favorite", "favourite", "love", "hate", "avoid", "allergic",
        "interested", "diet",
    )

    def _preference_block(self, user: str, query: str, facts: list[Fact], limit: int = 24) -> str:
        """Structured preference evidence: polarity/date/value rows rather than loose prose."""
        live = [f for f in self._all_facts() if f.user_id == user and f.is_live()]
        pool = {f.id: f for f in facts}
        for f in live:
            p = f.predicate.lower()
            if any(m in p for m in self._PREFERENCE_MARKERS):
                pool.setdefault(f.id, f)
        prefs = [f for f in pool.values() if any(m in f.predicate.lower() for m in self._PREFERENCE_MARKERS)]
        if not prefs:
            return ""
        prefs.sort(key=lambda f: (not overlap_terms(query, f"{f.predicate} {f.object}"), -f.valid_at))
        lines = ["date | subject | preference | value", "--- | --- | --- | ---"]
        for f in prefs[:limit]:
            lines.append(f"{fmt_date(f.valid_at)} | {f.subject} | {f.predicate.replace('_', ' ')} | {f.object}")
        return "PREFERENCE RECORDS (current, structured):\n" + "\n".join(lines)

    @staticmethod
    def _chain_terms(f: Fact) -> str:
        pred = f.predicate.replace("_", " ")
        aliases = ""
        if f.predicate.lower() in {"works_at", "employer", "company"}:
            aliases = " work works worked employer employed employment company job"
        elif f.predicate.lower() in {"lives_in", "location", "city", "home"}:
            aliases = " live lives lived location city home"
        return f"{f.subject} {pred} {f.object} {f.text} {aliases}"

    def _history_block(
        self,
        user: str,
        query: str,
        as_of: Optional[float],
        limit: int = 16,
        redact_sensitive: bool = False,
    ) -> str:
        """Supersession evidence for knowledge-update questions.

        Normal retrieval intentionally filters to the live/as-of view. History questions need the
        non-destructive chain as evidence: the old value, when it stopped being valid, and the current
        replacement that superseded it.
        """
        facts = [f for f in self._all_facts() if f.user_id == user]
        if redact_sensitive:
            facts = [f for f in facts if not getattr(f, "sensitive", False)]
        if not facts:
            return ""

        by_id = {f.id: f for f in facts}
        replaces: dict[str, list[Fact]] = {}
        for f in facts:
            if f.supersedes:
                replaces.setdefault(f.supersedes, []).append(f)

        selected: dict[str, Fact] = {}

        def add_chain(f: Fact) -> None:
            selected[f.id] = f
            if f.supersedes and f.supersedes in by_id:
                selected[f.supersedes] = by_id[f.supersedes]
            for newer in replaces.get(f.id, []):
                selected[newer.id] = newer

        for f in facts:
            is_chain_member = (
                f.invalid_at is not None
                or f.expired_at is not None
                or f.supersedes is not None
                or f.id in replaces
            )
            if not is_chain_member:
                continue
            if overlap_terms(query, self._chain_terms(f)):
                add_chain(f)
                continue
            if any(overlap_terms(query, self._chain_terms(newer)) for newer in replaces.get(f.id, [])):
                add_chain(f)

        if not selected:
            return ""

        rows = sorted(
            selected.values(),
            key=lambda f: (f.subject.lower(), f.predicate.lower(), f.valid_at, f.object.lower()),
        )[:limit]
        lines = [
            "valid from | valid until | subject | attribute | value | status",
            "--- | --- | --- | --- | --- | ---",
        ]
        for f in rows:
            until = fmt_date(f.invalid_at) if f.invalid_at is not None else "current"
            view_time = now() if as_of is None else as_of
            if f.valid_at > view_time:
                status = "not yet valid"
            elif f.is_live(as_of):
                status = "current"
            elif f.id in replaces:
                status = "superseded"
            else:
                status = "past"
            lines.append(
                f"{fmt_date(f.valid_at)} | {until} | {f.subject} | "
                f"{f.predicate.replace('_', ' ')} | {f.object} | {status}"
            )
        return "FACT HISTORY (supersession chain for update/previous-value questions):\n" + "\n".join(lines)

    @staticmethod
    def _mentions_value(query: str, value: str) -> bool:
        q = query.lower()
        v = value.lower().strip()
        if not v:
            return False
        if v in q:
            return True
        value_terms = set(stems(value))
        return bool(value_terms) and value_terms <= set(stems(query))

    def _temporal_history_result(
        self,
        query: str,
        user: str,
        as_of: Optional[float],
    ) -> Optional[SearchResult]:
        """Direct-answer path for natural-language history queries.

        `as_of()` already answers point-in-time questions when the caller supplies a timestamp. Users also
        ask in language: "Where did Wei work before Moonshot AI?" or "Where did Wei previously work?".
        Hybrid retrieval intentionally sees only live facts, so this resolver reads the non-destructive
        supersession chain before the live-only retriever gets a chance to answer with the current value.
        """
        if not self.config.temporal_history_queries or not _TEMPORAL_HISTORY_RE.search(query):
            return None
        view_time = now() if as_of is None else as_of
        facts = [f for f in self._all_facts() if f.user_id == user and f.valid_at <= view_time]
        if not facts:
            return None
        by_id = {f.id: f for f in facts}
        replacements: dict[str, list[Fact]] = {}
        for f in facts:
            if f.supersedes and f.supersedes in by_id:
                replacements.setdefault(f.supersedes, []).append(f)

        candidates: list[tuple[int, float, Fact, Fact]] = []
        for old, newers in ((by_id[old_id], ns) for old_id, ns in replacements.items()):
            visible_newers = [n for n in newers if n.valid_at <= view_time]
            if not visible_newers:
                continue
            newer = max(visible_newers, key=lambda f: (f.valid_at, f.created_at, f.id))
            if old.is_live(as_of):
                continue
            old_terms = self._chain_terms(old)
            newer_terms = self._chain_terms(newer)
            overlap = overlap_terms(query, old_terms) | overlap_terms(query, newer_terms)
            score = len(overlap)
            if self._mentions_value(query, newer.object):
                score += 6
            if self._mentions_value(query, old.subject):
                score += 2
            if overlap_terms(query, old.predicate.replace("_", " ")):
                score += 3
            if score <= 0:
                continue
            candidates.append((score, old.valid_at, old, newer))
        if not candidates:
            return None
        _, _, old, newer = max(candidates, key=lambda row: (row[0], row[1], row[2].created_at, row[2].id))
        reinforce(old, self.config.access_boost)
        return SearchResult(query=query, facts=[old, newer], scores=[1.0, 1.0], via="history", _answer=old.object)

    def _chain_facts_for_seeds(
        self,
        seeds: list[Fact],
        user: str,
        as_of: Optional[float],
        limit: int = 12,
        redact_sensitive: bool = False,
    ) -> list[Fact]:
        """Expand retrieved facts to their visible supersession chain.

        This is the read-path half of non-destructive invalidation: retrieval still ranks only the live
        slot head, but context assembly can show the bounded chain that explains what it replaced. The
        `as_of` guard is intentionally valid-time based to match existing point-in-time search semantics,
        so a past view never leaks a future replacement.
        """
        if not seeds:
            return []
        view_time = now() if as_of is None else as_of
        facts = [f for f in self._all_facts() if f.user_id == user]
        if redact_sensitive:
            facts = [f for f in facts if not getattr(f, "sensitive", False)]
        by_id = {f.id: f for f in facts}
        replaces: dict[str, list[Fact]] = {}
        for f in facts:
            if f.supersedes:
                replaces.setdefault(f.supersedes, []).append(f)

        def visible(f: Fact) -> bool:
            return f.valid_at <= view_time

        selected: dict[str, Fact] = {}

        def add_visible(f: Fact) -> None:
            if visible(f):
                selected.setdefault(f.id, f)

        def add_chain(seed: Fact) -> None:
            add_visible(seed)
            cur = seed
            seen: set[str] = {seed.id}
            while cur.supersedes and cur.supersedes in by_id and cur.supersedes not in seen:
                cur = by_id[cur.supersedes]
                seen.add(cur.id)
                add_visible(cur)
            queue = [seed]
            while queue:
                cur = queue.pop(0)
                for newer in sorted(replaces.get(cur.id, []), key=lambda f: f.valid_at):
                    if newer.id in seen:
                        continue
                    seen.add(newer.id)
                    add_visible(newer)
                    queue.append(newer)

        for seed in seeds:
            add_chain(seed)

        # A single visible fact is already shown in FACTS; only render when the chain adds explanatory
        # evidence such as a superseded value or a visible replacement.
        if len(selected) <= 1:
            return []
        return sorted(selected.values(), key=lambda f: (f.subject.lower(), f.predicate.lower(), f.valid_at))[:limit]

    def _fact_evolution_block(
        self,
        seeds: list[Fact],
        user: str,
        as_of: Optional[float],
        query: str = "",
        limit: int = 12,
        redact_sensitive: bool = False,
    ) -> str:
        if query:
            seeds = [f for f in seeds if overlap_terms(query, self._chain_terms(f))]
        rows = self._chain_facts_for_seeds(
            seeds,
            user,
            as_of,
            limit=limit,
            redact_sensitive=redact_sensitive,
        )
        if not rows:
            return ""
        replaced_ids = {f.supersedes for f in self._all_facts() if f.supersedes}

        def _role(f: Fact) -> str:
            if f.is_live(as_of):
                return "current"
            if f.id in replaced_ids:
                return "superseded"
            return "replacement" if f.supersedes else "past"

        if self.config.chain_current_first:
            # Group by slot and lead with the CURRENT value, history indented beneath it. A flat table
            # makes the reader scan a `role` column to find which row is true now, and on knowledge-update
            # questions the answerer picked the superseded value often enough to lose to plain
            # full-context (e.g. answering the old city after a documented move). Stating the current
            # value first, as a claim rather than a row, removes that scan.
            slots: dict[tuple[str, str], list[Fact]] = {}
            for f in rows:
                slots.setdefault((f.subject, f.predicate), []).append(f)
            blocks = []
            for (subj, pred), facts in slots.items():
                facts.sort(key=lambda x: x.valid_at, reverse=True)
                attr = pred.replace("_", " ")
                live = next((f for f in facts if f.is_live(as_of)), None)
                head = live or facts[0]
                label = "CURRENT" if live is not None else f"LATEST KNOWN ({_role(head)})"
                blocks.append(f"{label}: {subj} {attr} = {head.object}  (since {fmt_date(head.valid_at)})")
                for f in facts:
                    if f is head:
                        continue
                    until = fmt_date(f.invalid_at) if f.invalid_at is not None else "?"
                    blocks.append(
                        f"    was: {f.object}  ({fmt_date(f.valid_at)} - {until}, {_role(f)})"
                    )
            return ("FACT EVOLUTION (current value first, then what it replaced):\n"
                    + "\n".join(blocks))

        lines = [
            "valid from | valid until | subject | attribute | value | role",
            "--- | --- | --- | --- | --- | ---",
        ]
        for f in rows:
            until = fmt_date(f.invalid_at) if f.invalid_at is not None else "current"
            lines.append(
                f"{fmt_date(f.valid_at)} | {until} | {f.subject} | "
                f"{f.predicate.replace('_', ' ')} | {f.object} | {_role(f)}"
            )
        return "FACT EVOLUTION (retrieved supersession chain):\n" + "\n".join(lines)

    @staticmethod
    def _snippet_for_evidence(content: str, needle: str, max_chars: int = 360) -> str:
        """Return a compact raw-evidence slice, preferring lines/sentences that match the query/fact.

        Raw provenance is valuable because fact extraction can drop qualifiers, counts, or surrounding
        details. The slice is intentionally tiny so provenance evidence improves recall without quietly
        turning the lean path into full-history replay.
        """
        text = " ".join(content.split())
        if len(text) <= max_chars:
            return text
        parts = [p.strip() for p in re.split(r"(?<=[.!?。！？])\s+|\n+", content) if p.strip()]
        best = ""
        best_score = -1
        for part in parts:
            score = len(overlap_terms(needle, part))
            if score > best_score:
                best = part
                best_score = score
        snippet = " ".join((best or text).split())
        if len(snippet) <= max_chars:
            return snippet
        return snippet[: max_chars - 1].rstrip() + "…"

    def _provenance_raw_block(
        self,
        facts: list[Fact],
        query: str,
        user: str,
        as_of: Optional[float],
        exclude_episode_ids: set[str],
        limit: int = 4,
        snippet_chars: int = 360,
        redact_sensitive: bool = False,
    ) -> str:
        """Compact source episodes for retrieved facts.

        This is the raw side of hybrid memory: facts give precise, conflict-resolved slots; their
        provenance episodes restore dropped detail. We only use episodes explicitly cited by retrieved
        facts, exclude already-rendered full-detail chunks, obey `as_of`, and disable the block for
        redacted contexts because raw prose can contain sensitive material outside fact classifiers.
        """
        if redact_sensitive or not facts:
            return ""
        rows: list[str] = []
        seen_eps: set[str] = set(exclude_episode_ids)
        view_time = now() if as_of is None else as_of
        history_query = bool(_TEMPORAL_HISTORY_RE.search(query or ""))

        def fact_order(f: Fact) -> tuple[int, float]:
            past = int(history_query and f.valid_at <= view_time and not f.is_live(as_of))
            return (past, f.valid_at)

        for fact in sorted(facts, key=fact_order, reverse=True):
            if getattr(fact, "sensitive", False):
                continue
            if query and not overlap_terms(query, self._chain_terms(fact)):
                continue
            for ep_id in fact.provenance:
                if ep_id in seen_eps:
                    continue
                ep = self.episodes_doc.get(ep_id)
                if ep is None or ep.user_id != user or ep.event_time > view_time:
                    continue
                seen_eps.add(ep_id)
                needle = f"{query} {fact.text} {fact.subject} {fact.predicate} {fact.object}"
                snippet = self._snippet_for_evidence(ep.content, needle, max_chars=snippet_chars)
                date = ep.metadata.get("date") or fmt_date(ep.event_time)
                rows.append(
                    f"- [{date}] (session: {ep.session_id}; supports: "
                    f"{fact.subject} {fact.predicate.replace('_', ' ')} {fact.object}) {snippet}"
                )
                if len(rows) >= limit:
                    return "PROVENANCE RAW EVIDENCE (source episodes for retrieved facts):\n" + "\n".join(rows)
        return "PROVENANCE RAW EVIDENCE (source episodes for retrieved facts):\n" + "\n".join(rows) if rows else ""


    @staticmethod
    def _merge_promoted_chunks(promoted: list, retrieved: list, k: int) -> list:
        """Merge provenance-promoted source episodes with semantically-retrieved ones under a
        semantic floor: promotion may take at most half the chunk budget.

        Promoted chunks are keyed off the ranked FACTS, so when fact retrieval misses, the
        semantic episodes are the only path that still carries the answer. Letting promotion
        fill the whole budget evicted exactly those episodes and produced correct->abstain
        regressions on single-session recall (50-item A/B vs the headline log, 2026-08-27);
        capping it keeps the previous-value/temporal gains without paying for them there.
        Unused promotion budget flows back to retrieved chunks, and vice versa. With k=1 the
        single slot stays promotion-first (the pre-fix behavior, which the regression data —
        gathered at k=2 — says nothing about, and which existing tests pin).
        """
        cap = max(1, k // 2)
        merged: list = []
        seen: set[str] = set()
        for ep in promoted[:cap]:
            if ep.id not in seen:
                seen.add(ep.id)
                merged.append(ep)
        for ep in retrieved:
            if len(merged) >= k:
                break
            if ep.id not in seen:
                seen.add(ep.id)
                merged.append(ep)
        for ep in promoted[cap:]:  # backfill only if retrieval could not fill the budget
            if len(merged) >= k:
                break
            if ep.id not in seen:
                seen.add(ep.id)
                merged.append(ep)
        return merged

    def _provenance_detail_chunks(
        self,
        facts: list[Fact],
        query: str,
        user: str,
        as_of: Optional[float],
        limit: int,
        redact_sensitive: bool = False,
    ) -> list[Episode]:
        """Full raw chunks backed by retrieved facts' provenance.

        Normal raw retrieval is query-first. If it is distracted, a fact can still pinpoint the right
        source episode through provenance; promoting that episode restores surrounding details while the
        same `n_chunks` budget keeps the read path lean.
        """
        if limit <= 0 or redact_sensitive or not facts:
            return []
        view_time = now() if as_of is None else as_of
        history_query = bool(_TEMPORAL_HISTORY_RE.search(query or ""))
        scored: dict[str, tuple[int, int, float, Episode]] = {}
        for fact_rank, fact in enumerate(facts):
            if getattr(fact, "sensitive", False):
                continue
            fact_terms = self._chain_terms(fact)
            fact_overlap = len(overlap_terms(query, fact_terms)) if query else 0
            if query and fact_overlap == 0:
                continue
            for ep_id in fact.provenance:
                ep = self.episodes_doc.get(ep_id)
                if ep is None or ep.user_id != user or ep.event_time > view_time:
                    continue
                ep_overlap = len(overlap_terms(query, ep.content)) if query else 0
                support_overlap = len(overlap_terms(fact.text, ep.content))
                score = fact_overlap * 8 + support_overlap * 3 + ep_overlap * 2
                if history_query and fact.valid_at <= view_time and not fact.is_live(as_of):
                    score += 96
                prev = scored.get(ep.id)
                item = (score, fact_rank, ep.event_time, ep)
                if prev is None or (score, -fact_rank, ep.event_time) > (prev[0], -prev[1], prev[2]):
                    scored[ep.id] = item
        ranked = sorted(scored.values(), key=lambda item: (-item[0], item[1], -item[2], item[3].id))
        return [ep for _, _, _, ep in ranked[:limit]]

    def _aggregation_block(
        self,
        facts: list[Fact],
        summaries: list[Episode],
        detail_eps: list[Episode],
        query: str = "",
        limit: int = 36,
    ) -> str:
        """Broad evidence table for count/list/across-session questions."""
        rows: list[tuple[int, float, str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add_row(t: float, source: str, evidence: str) -> None:
            if source in {"conversation", "summary"} and query:
                evidence = self._snippet_for_evidence(evidence, query, max_chars=520)
            text = " ".join(evidence.split())
            if not text:
                return
            key = (fmt_date(t), text.lower()[:140])
            if key in seen:
                return
            seen.add(key)
            score = len(overlap_terms(query, text)) if query else 0
            if query and score == 0:
                return
            if source == "fact":
                score += 2
            elif source == "conversation":
                score += 1
            rows.append((score, t, source, text[:520]))

        for f in facts:
            add_row(f.valid_at, "fact", f.text)
        for ep in summaries:
            add_row(ep.event_time, "summary", ep.summary or ep.content)
        for ep in detail_eps:
            add_row(ep.event_time, "conversation", ep.content)
        if not rows:
            return ""
        rows.sort(key=lambda x: (-x[0], x[1]))
        lines = ["date | source | evidence", "--- | --- | ---"]
        for _, t, source, evidence in rows[:limit]:
            lines.append(f"{fmt_date(t)} | {source} | {evidence}")
        evidence = (
            "AGGREGATION EVIDENCE (dedupe before counting/listing; include candidate names and key "
            "attributes in the answer):\n" + "\n".join(lines)
        )
        candidates = render_aggregation_candidates(
            extract_aggregation_candidates(
                query,
                facts,
                summaries + detail_eps,
                llm=self.llm,
                numeric=self.config.numeric_aggregation_candidates,
                constraint_filter=self.config.aggregation_constraint_filter,
            )
        )
        return f"{candidates}\n\n{evidence}" if candidates else evidence

    def _stamp(self, f: Fact, temporal: bool = False) -> str:
        """Date label for a fact: the absolute stamp, plus -- only for time-shaped questions -- the
        source's own wording.

        valid_at is a single point because retrieval and conflict resolution need one; but a temporal
        question often asks for the phrasing ("the week before 9 June", "first weekend of August"), and
        answering with the stamp alone was wrong on every LOCOMO granularity failure.

        Gated on `temporal` because the first version rendered the phrase on EVERY fact line: LOCOMO
        temporal gained 2 items while single-hop lost 6, four of them turning into abstentions and one
        answering with a date the question never asked about. The wording is signal when the question is
        about time and clutter otherwise, so it is shown only when the evidence planner says so.
        """
        d = fmt_date(f.valid_at)
        if not (temporal and self.config.temporal_phrase_preservation):
            return d
        phrase = getattr(f, "time_phrase", "")
        return f'{d} · said: "{phrase}"' if phrase else d

    def _duration_block(
        self,
        query: str,
        facts: list[Fact],
        episodes: list[Episode],
        limit: int = 24,
    ) -> str:
        """Dated raw snippets for duration questions, where start/finish pairs are often lost in summaries."""
        anchors = [a or b for a, b in re.findall(r"'([^']+)'|\"([^\"]+)\"", query)]
        anchors = [a.strip() for a in anchors if a and len(a.strip()) >= 3]
        if not anchors:
            anchors = sorted(overlap_terms(query, query), key=len, reverse=True)[:3]
        action_re = re.compile(
            r"\b(start(?:ed)?|begin|began|finish(?:ed)?|complete(?:d)?|listen(?:ed|ing)?|read(?:ing)?|"
            r"watch(?:ed|ing)?|today|last|week|weeks|month|months|day|days)\b",
            re.IGNORECASE,
        )
        rows: list[tuple[float, str, str]] = []
        seen: set[tuple[str, str, str]] = set()

        def add_row(t: float, anchor: str, source: str, evidence: str) -> None:
            text = " ".join(evidence.split())
            if not text:
                return
            key = (fmt_date(t), anchor.lower(), text.lower()[:180])
            if key in seen:
                return
            seen.add(key)
            rows.append((t, anchor, f"{source}: {text[:280]}"))

        for f in facts:
            lower = f.text.lower()
            for anchor in anchors:
                if anchor.lower() in lower:
                    add_row(f.valid_at, anchor, "fact", f.text)

        split_re = re.compile(r"(?<=[.!?])\s+|\n+")
        for ep in episodes:
            parts = split_re.split(ep.content)
            for anchor in anchors:
                al = anchor.lower()
                for part in parts:
                    pl = part.lower()
                    if al not in pl:
                        continue
                    if not action_re.search(part):
                        continue
                    add_row(ep.event_time, anchor, "conversation", part)
                    break

        if not rows:
            return ""
        rows.sort(key=lambda x: (x[1].lower(), x[0]))
        kept = rows[:limit]
        lines = ["date | item | evidence", "--- | --- | ---"]
        for t, anchor, evidence in kept:
            lines.append(f"{fmt_date(t)} | {anchor} | {evidence}")
        out = (
            "DURATION EVIDENCE (pair start/finish dates per item; compute each end-start, then sum):\n"
            + "\n".join(lines)
        )
        spans = self._span_lines(kept) if self.config.temporal_span_block else []
        if spans:
            # The old block handed over dates and told the model to subtract them. It does that badly:
            # on LOCOMO every "how long did X take" failure abstained or was off by months, and the gold
            # ("four months") appears nowhere in the haystack -- it IS the difference between two session
            # dates. Doing the arithmetic here turns a computation the answerer fails into a fact it reads.
            out += "\n\nTIME SPANS (already computed, same item, earliest -> latest):\n" + "\n".join(spans)
        return out

    @staticmethod
    def _span_lines(rows: list[tuple[float, str, str]], max_items: int = 6) -> list[str]:
        """Day/month spans between the first and last dated evidence for each anchor."""
        by_anchor: dict[str, list[float]] = {}
        for t, anchor, _ in rows:
            by_anchor.setdefault(anchor.lower(), []).append(t)
        lines: list[str] = []
        for anchor, times in list(by_anchor.items())[:max_items]:
            if len(times) < 2:
                continue
            lo, hi = min(times), max(times)
            days = int(round((hi - lo) / DAY))
            if days <= 0:
                continue
            months = days / 30.44
            approx = f"~{months:.1f} months" if days >= 45 else f"~{days // 7} weeks" if days >= 14 else ""
            suffix = f" ({approx})" if approx else ""
            lines.append(f"{anchor}: {fmt_date(lo)} -> {fmt_date(hi)} = {days} days{suffix}")
        return lines

    def _graph_paths_block(
        self,
        query: str,
        user: str,
        as_of: Optional[float],
        limit: int = 12,
        redact_sensitive: bool = False,
    ) -> str:
        """Bounded graph path evidence from query anchors; enough structure for relation-chain questions."""
        lines: list[str] = []
        plan = self.planner.plan(query, user, as_of)
        if plan is not None and plan.facts:
            for f in plan.facts[:limit]:
                if redact_sensitive and getattr(f, "sensitive", False):
                    continue
                lines.append(f"- [{self._stamp(f, True)}] {f.text}")
            if lines:
                return "GRAPH PATHS (relation evidence):\n" + "\n".join(lines)
            return ""
        if not self.config.graph_proximity:
            return ""
        qids = self.retriever.query_entity_ids(query, user)
        excluded = self.retriever.graph_exclusion_zone(query, user, as_of)
        frontier: list[tuple[str, int]] = [(eid, 0) for eid in qids]
        seen_nodes = set(qids)
        seen_edges: set[str] = set()
        max_hops = max(0, self.config.max_hops)
        while frontier and len(lines) < limit:
            eid, depth = frontier.pop(0)
            if depth > max_hops:
                continue
            for direction in ("out", "in"):
                for rel in self.graph.neighbors(eid, as_of, direction):
                    if rel.id in seen_edges:
                        continue
                    if rel.subject_id in excluded or rel.object_id in excluded:
                        continue
                    f = self.fact_store.get(rel.fact_id) or self.cold_store.get(rel.fact_id)
                    if f is None or not f.is_live(as_of):
                        continue
                    if redact_sensitive and getattr(f, "sensitive", False):
                        continue
                    seen_edges.add(rel.id)
                    subj = self.graph.entities.get(rel.subject_id)
                    obj = self.graph.entities.get(rel.object_id)
                    lines.append(
                        f"- [{fmt_date(f.valid_at)}] {subj.name if subj else f.subject} "
                        f"--{rel.predicate}--> {obj.name if obj else f.object}"
                    )
                    neighbor_id = rel.object_id if direction == "out" else rel.subject_id
                    if depth < max_hops and neighbor_id not in seen_nodes:
                        seen_nodes.add(neighbor_id)
                        frontier.append((neighbor_id, depth + 1))
                    if len(lines) >= limit:
                        return "GRAPH PATHS (relation evidence):\n" + "\n".join(lines)
        return "GRAPH PATHS (relation evidence):\n" + "\n".join(lines) if lines else ""

    @staticmethod
    def _evidence_block_title(block: str) -> str:
        return block.split("\n", 1)[0].rstrip(":")

    @classmethod
    def _evidence_block_kind(cls, block: str) -> str:
        title = cls._evidence_block_title(block)
        if title.startswith("PROVENANCE RAW"):
            return "provenance"
        if title.startswith("RELEVANT CONVERSATIONS"):
            return "raw"
        if title.startswith("GRAPH PATHS"):
            return "graph"
        if title.startswith("AGGREGATION"):
            return "aggregation"
        if title.startswith("DURATION"):
            return "duration"
        if title.startswith("FACT HISTORY"):
            return "history"
        if title.startswith("FACT EVOLUTION"):
            return "evolution"
        if title.startswith("CURRENT STATE"):
            return "current_state"
        if title.startswith("PREFERENCE RECORDS"):
            return "preference"
        if title.startswith("PROCEDURAL MEMORY"):
            return "procedural"
        if title.startswith("FACTS"):
            return "facts"
        if title.startswith("SESSION SUMMARIES"):
            return "summaries"
        if title.startswith("TIMELINE"):
            return "timeline"
        if title.startswith("WORKING MEMORY"):
            return "working"
        if title.startswith("USER PROFILE"):
            return "profile"
        return "other"

    @staticmethod
    def _fit_evidence_block(block: str, cap: int) -> str:
        if cap <= 0:
            return ""
        if len(block) <= cap:
            return block
        title, _, body = block.partition("\n")
        if cap <= len(title):
            return title[:cap]
        return (title + "\n" + body[: cap - len(title) - 1]).rstrip()

    def _evidence_priority(self, kind: str, need) -> int:
        score = {
            "working": 95,
            "facts": 90,
            "current_state": 88,
            "procedural": 86,
            "provenance": 82,
            "raw": 78,
            "graph": 76,
            "history": 75,
            "evolution": 72,
            "timeline": 68,
            "aggregation": 66,
            "duration": 66,
            "preference": 66,
            "summaries": 58,
            "profile": 38,
            "other": 40,
        }.get(kind, 40)
        if need is None:
            return score
        if need.exact_lookup:
            score += {"provenance": 45, "raw": 35, "facts": 20}.get(kind, 0)
        if need.multi_hop:
            score += {"graph": 45, "facts": 25, "provenance": 15, "raw": 12}.get(kind, 0)
        if need.aggregation:
            score += {"aggregation": 55, "raw": 35, "summaries": 30, "facts": 10}.get(kind, 0)
        if need.duration:
            score += {"duration": 55, "raw": 35, "timeline": 25, "summaries": 15}.get(kind, 0)
        if need.history:
            score += {"history": 55, "evolution": 40, "timeline": 20, "facts": 15}.get(kind, 0)
        if getattr(need, "procedural", False):
            score += {"procedural": 55, "facts": 18, "provenance": 15, "raw": 12, "summaries": 8}.get(kind, 0)
        if need.current_state:
            score += {"current_state": 45, "facts": 20, "evolution": 20}.get(kind, 0)
        if need.preference:
            score += {"preference": 45, "facts": 18, "provenance": 12, "raw": 10}.get(kind, 0)
        if need.timeline:
            score += {"timeline": 35, "history": 15, "facts": 10}.get(kind, 0)
        return score

    def _evidence_block_cap(self, kind: str, budget: int, need) -> int:
        ratio = {
            "aggregation": 0.60,
            "duration": 0.55,
            "provenance": 0.55,
            "raw": 0.55,
            "graph": 0.45,
            "history": 0.45,
            "evolution": 0.42,
            "facts": 0.42,
            "timeline": 0.35,
            "summaries": 0.35,
            "current_state": 0.35,
            "procedural": 0.38,
            "preference": 0.35,
            "working": 0.25,
            "profile": 0.18,
            "other": 0.25,
        }.get(kind, 0.25)
        if need is not None and need.exact_lookup and kind in {"provenance", "raw"}:
            ratio = 0.75
        if need is not None and need.aggregation and kind == "aggregation":
            ratio = 0.75
        cap = int(max(96, budget * ratio))
        return min(cap, budget)

    def _fit_blocks_even(self, selected: list[str], budget: int) -> str:
        if not selected or budget <= 0:
            return ""
        sep = "\n\n"
        sep_chars = len(sep) * (len(selected) - 1)
        if budget <= sep_chars:
            return sep.join(block.split("\n", 1)[0] for block in selected)[:budget]
        available = budget - sep_chars
        quota, extra = divmod(available, len(selected))
        fitted: list[str] = []
        for i, block in enumerate(selected):
            cap = quota + (1 if i < extra else 0)
            fitted.append(self._fit_evidence_block(block, cap))
        return sep.join(fitted)[:budget]

    def _fit_blocks_by_evidence_budget(self, blocks: list[str], budget: int, need) -> str:
        """Intent-aware evidence packer for the lean read path.

        The old fallback split a tight budget evenly across blocks or raw-truncated from the top, which
        can spend precious tokens on profile text while dropping the exact raw/provenance/graph evidence
        the question needs. This ranks evidence classes by query intent, caps each class, and then emits
        the selected evidence in the original readable order.
        """
        if not blocks or budget <= 0:
            return ""
        sep = "\n\n"
        ranked = sorted(
            enumerate(blocks),
            key=lambda item: (-self._evidence_priority(self._evidence_block_kind(item[1]), need), item[0]),
        )
        selected: list[tuple[int, str]] = []
        used = 0
        for idx, block in ranked:
            kind = self._evidence_block_kind(block)
            priority = self._evidence_priority(kind, need)
            sep_cost = len(sep) if selected else 0
            remaining = budget - used - sep_cost
            if remaining <= 0:
                break
            if selected and remaining < 120 and priority < 100:
                continue
            cap = min(len(block), self._evidence_block_cap(kind, budget, need), remaining)
            fitted = self._fit_evidence_block(block, cap)
            if not fitted:
                continue
            min_title = self._evidence_block_title(block)
            if selected and len(fitted) < min(len(min_title), max(24, remaining)):
                continue
            selected.append((idx, fitted))
            used += sep_cost + len(fitted)
        if not selected:
            return self._fit_evidence_block(blocks[0], budget)
        return sep.join(block for _, block in sorted(selected, key=lambda item: item[0]))[:budget]

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
        redact_sensitive: bool = False,  # safe/shared view: only non-sensitive structured facts are shown
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
        need = (
            plan_evidence(
                query,
                aggregation_recall_expansion=self.config.aggregation_recall_expansion,
            )
            if self.config.evidence_planner
            else None
        )
        if need is not None:
            n_facts = max(n_facts, need.n_facts)
            n_summaries = max(n_summaries, need.n_summaries)
            n_chunks = max(n_chunks, need.n_chunks)
            agentic = agentic or need.use_agentic
            cascade = cascade or need.use_cascade
            timeline = timeline or need.timeline
        if redact_sensitive:
            # Free-text layers (persona, summaries, raw chunks, working memory) can fold sensitive content
            # into prose. A redacted context is therefore deliberately facts-only, after fact-level
            # sensitivity classification, so shared/third-party injection has a crisp privacy boundary.
            n_summaries = 0
            n_chunks = 0
            cascade = False

        # Bet B — multi-hop decomposition. For a relational/aggregation question, an LLM splits it into
        # sub-queries ('who is my colleague' + 'where does <colleague> work'); we then retrieve facts AND
        # summaries for each angle and union them, so 2nd-hop evidence that the single query can't surface
        # gets pulled in. This is the field's weak spot (multi-session/multi-hop) and our attack surface.
        queries = [query]
        if need is not None:
            for sq in need.subqueries:
                if sq not in queries:
                    queries.append(sq)
        if agentic and self.llm is not None:
            from .retrieve.agentic import AgenticRetriever
            queries += AgenticRetriever(self, self.llm)._subqueries(query)

        # The L3 persona is free-text synthesis and may fold in sensitive facts; redacted contexts are
        # structured-facts-only, so we drop it entirely.
        if persona and not redact_sensitive:
            p = self._persona_at(user, as_of)
            if p:
                label = "USER PROFILE" if as_of is None else f"USER PROFILE (as of {fmt_date(as_of)})"
                blocks.append(f"{label}:\n{p}")

        # WORKING MEMORY: the current session's ephemeral state ("today my throat hurts", this-trip intent)
        # — surfaced so the answer reflects "right now", but never consolidated to long-term or the profile.
        if session_id is not None and not redact_sensitive:
            wm = self.working_memory(user, session_id=session_id, as_of=as_of)
            if wm:
                wl = "\n".join(f"- [{w.kind}] {w.content}"
                               for w in sorted(wm, key=lambda x: x.event_time))
                blocks.append(f"WORKING MEMORY (this session, ephemeral):\n{wl}")

        # L1 facts: hybrid retrieval per (sub-)query, unioned; + n-hop graph expansion from query entities.
        fact_map: dict[str, Fact] = {}
        for q in queries:
            ranked = self.retriever.retrieve(q, user, as_of, top_k or n_facts)[0]
            if not ranked:
                ranked = self._retrieve_cold(q, user, as_of, top_k or n_facts)[0]
            for f, _ in ranked:
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
            # When the planner is off there is no `need`, so fall back to the query itself -- a
            # time-shaped question must still get the wording, planner or not.
            _t = (bool(need.timeline or need.duration or need.history) if need is not None
                  else bool(re.search(r"\b(when|what\s+date|how\s+long|before|after|since|during|"
                                      r"first|last|earliest|latest|recent)\b", query, re.I)))
            fl = "\n".join(f"- [{self._stamp(f, _t)}] {f.text}" for f in by_date)
            blocks.append(f"FACTS (current, dated):\n{fl}")
            if need is not None and need.current_state:
                state = self._current_state_block(all_facts)
                if state:
                    blocks.append(state)
            if need is not None and need.preference:
                prefs = self._preference_block(user, query, all_facts)
                if prefs:
                    blocks.append(prefs)
            # TIMELINE: the same facts oldest->newest with explicit gaps, so 'first / most-recent / how long
            # between' is read off the order and the date arithmetic is set up for the model rather than
            # left to mental math (the temporal category's main failure mode).
            if timeline:
                chrono = sorted(all_facts, key=lambda f: f.valid_at)
                tl = "\n".join(f"- {fmt_date(f.valid_at)}: {f.text}" for f in chrono)
                blocks.append(f"TIMELINE (oldest to newest — use for ordering and durations):\n{tl}")
            if need is not None and need.multi_hop:
                paths = self._graph_paths_block(query, user, as_of, redact_sensitive=redact_sensitive)
                if paths:
                    blocks.append(paths)
            if self.config.chain_evidence and (need is None or not need.history):
                evo = self._fact_evolution_block(
                    all_facts,
                    user,
                    as_of,
                    query=query,
                    limit=max(8, min(18, n_facts + 3)),
                    redact_sensitive=redact_sensitive,
                )
                if evo:
                    blocks.append(evo)

        provenance_facts = list(all_facts)
        if self.config.chain_evidence and all_facts:
            seen_chain_facts = {f.id for f in provenance_facts}
            for f in self._chain_facts_for_seeds(
                all_facts,
                user,
                as_of,
                limit=max(8, min(18, n_facts + 3)),
                redact_sensitive=redact_sensitive,
            ):
                if f.id not in seen_chain_facts:
                    seen_chain_facts.add(f.id)
                    provenance_facts.append(f)

        if (
            self.config.procedural_memory
            and not redact_sensitive
            and need is not None
            and getattr(need, "procedural", False)
        ):
            proc = self._procedural_memory_block(
                query,
                user,
                as_of,
                limit=getattr(self.config, "procedural_memory_k", 6),
            )
            if proc:
                blocks.append(proc)

        if need is not None and need.history and self.config.temporal_history_queries:
            hist = self._history_block(user, query, as_of, redact_sensitive=redact_sensitive)
            if hist:
                blocks.append(hist)

        # L2 coarse: retrieve session summaries per (sub-)query, ranked. This is the coarse layer of a
        # coarse-to-fine cascade (CLAUDE.md Bet E / OpenViking): summaries are tiny, so we can index and
        # rank MANY sessions cheaply — the key to scaling past a model's window (the _M / 10M frontier).
        summ_ranked: list[Episode] = []
        seen_s: set[str] = set()
        if n_summaries:
            for q in queries:
                for e in self.retrieve_summaries(q, user, n_summaries, as_of=as_of):
                    if e.id not in seen_s:
                        seen_s.add(e.id)
                        summ_ranked.append(e)

        # FINE drill: in cascade mode the detail chunks are the TOP-ranked summaries' own sessions (score
        # propagates coarse->fine), so we never embed-scan raw turns of irrelevant sessions. Without
        # cascade, detail is a direct episode lookup (fine for small histories).
        if cascade and summ_ranked:
            detail_eps = summ_ranked[:n_chunks] if n_chunks else []
        else:
            detail_eps = []
            if n_chunks:
                seen_detail: set[str] = set()
                detail_queries = list(need.subqueries) + [query] if need is not None and need.subqueries else queries
                per_query = [
                    self.retrieve_episodes(q, user, max(n_chunks, 1), as_of=as_of)
                    for q in detail_queries
                ]
                max_hits = max((len(eps) for eps in per_query), default=0)
                for rank in range(max_hits):
                    for eps in per_query:
                        if rank >= len(eps):
                            continue
                        ep = eps[rank]
                        if ep.id in seen_detail:
                            continue
                        seen_detail.add(ep.id)
                        detail_eps.append(ep)
                        if len(detail_eps) >= n_chunks:
                            break
                    if len(detail_eps) >= n_chunks:
                        break
        detail_ids = {e.id for e in detail_eps}
        if self.config.provenance_chunk_promotion and n_chunks and not redact_sensitive:
            promoted = self._provenance_detail_chunks(
                provenance_facts,
                " ".join(queries),
                user,
                as_of,
                limit=n_chunks,
                redact_sensitive=redact_sensitive,
            )
            if promoted:
                detail_eps = self._merge_promoted_chunks(promoted, detail_eps, n_chunks)
                detail_ids = {e.id for e in detail_eps}
        summaries = [e for e in summ_ranked if e.id not in detail_ids]

        # Aggregation/list questions are especially vulnerable to compression loss: the right session can
        # be retrieved for consolidation, while the LLM fact/summary step drops one low-salience candidate.
        # Keep a compact raw-evidence table for these questions so counting/listing sees candidates before
        # they are squeezed through System-2 abstractions.
        aggregation_eps: list[Episode] = []
        if need is not None and need.aggregation and not redact_sensitive:
            seen_agg = set(detail_ids)
            for q in queries:
                for ep in self.retrieve_episodes(q, user, 4, as_of=as_of):
                    if ep.id in seen_agg:
                        continue
                    seen_agg.add(ep.id)
                    aggregation_eps.append(ep)
                    if len(aggregation_eps) >= 12:
                        break
                if len(aggregation_eps) >= 12:
                    break

        if summaries:
            chrono = sorted(summaries, key=lambda e: e.event_time)
            sm = "\n".join(
                f"- [{e.metadata.get('date') or fmt_date(e.event_time)}] {e.summary}" for e in chrono
            )
            blocks.append(f"SESSION SUMMARIES (relevant, chronological):\n{sm}")

        if detail_eps:
            chunks = "\n\n".join(
                f"[{e.metadata.get('date') or fmt_date(e.event_time)}] "
                f"(session: {e.session_id})\n{e.content}" for e in detail_eps
            )
            blocks.append(f"RELEVANT CONVERSATIONS (full detail):\n{chunks}")

        if self.config.provenance_evidence:
            raw_prov = self._provenance_raw_block(
                provenance_facts,
                query,
                user,
                as_of,
                exclude_episode_ids=detail_ids,
                redact_sensitive=redact_sensitive,
            )
            if raw_prov:
                blocks.append(raw_prov)

        if need is not None and need.duration:
            dur = self._duration_block(query, all_facts, detail_eps + aggregation_eps)
            if dur:
                blocks.append(dur)

        if need is not None and need.aggregation:
            agg = self._aggregation_block(
                all_facts,
                summaries,
                detail_eps + aggregation_eps,
                query=" ".join(queries),
            )
            if agg:
                blocks.append(agg)

        assembled = "\n\n".join(blocks)
        default_budget = char_budget == 60_000
        if char_budget == 60_000:
            full_chars = sum(len(ep.content) for ep in self.episodes_doc.values() if ep.user_id == user)
            if full_chars:
                # Tiny histories still need enough room for profile + facts + one chunk; otherwise the
                # even block fitter can cut short durable project rules exactly where the constraint lives.
                char_budget = min(char_budget, max(2048, full_chars * 3 - 1))
            if len(assembled) > char_budget:
                if self.config.evidence_budgeting:
                    return self._fit_blocks_by_evidence_budget(blocks, char_budget, need)
                return self._fit_blocks_even(blocks, char_budget)
        elif len(assembled) > char_budget and self.config.evidence_budgeting:
            return self._fit_blocks_by_evidence_budget(blocks, char_budget, need)
        return assembled[:char_budget]

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

        # 2. natural-language history queries over the supersession chain
        hist = self._temporal_history_result(query, user, as_of)
        if hist is not None:
            return hist

        # 3. typed procedural memory: for rule/how-to queries, prefer the source-backed procedural view over
        # a generic fact answer. This keeps standing instructions auditable and still falls through for
        # ordinary lookup questions.
        proc = self._procedural_fallback(query, user, as_of=as_of)
        if proc is not None:
            return proc

        # 4. hybrid retrieval
        ranked, diag = self.retriever.retrieve(query, user, as_of, top_k)
        via = "hybrid"
        if not ranked:
            ranked, diag = self._retrieve_cold(query, user, as_of, top_k)
            if ranked:
                via = "cold"
        if not ranked:
            proc = self._procedural_fallback(query, user, as_of=as_of)
            if proc is not None:
                return proc
            summ = self._summary_fallback(query, user_id, as_of=as_of)
            if summ is not None:
                return summ
            return SearchResult(query=query, via="abstain", abstained=True)

        facts = [f for f, _ in ranked]
        scores = [s for _, s in ranked]

        # #2/#3 answer-TYPE alignment: if the question demands a structured value, surface a fact whose
        # object actually looks like that type; if none does, the semantic hit is spurious -> not-in-memory.
        etype = _expected_answer_type(query)
        type_ok = True
        if etype is not None:
            matched = [f for f in facts if _ANSWER_TYPE_MATCH[etype](f.object or f.text)]
            if matched:
                facts = matched + [f for f in facts if f not in matched]
            else:
                type_ok = False

        if not type_ok or self._should_abstain(query, facts, diag):
            cold_ranked, cold_diag = self._retrieve_cold(query, user, as_of, top_k)
            if cold_ranked:
                facts = [f for f, _ in cold_ranked]
                scores = [s for _, s in cold_ranked]
                diag = cold_diag
                type_ok = True
                if etype is not None:
                    matched = [f for f in facts if _ANSWER_TYPE_MATCH[etype](f.object or f.text)]
                    if matched:
                        facts = matched + [f for f in facts if f not in matched]
                    else:
                        type_ok = False
                if type_ok and not self._should_abstain(query, facts, diag):
                    return SearchResult(query=query, facts=facts, scores=scores, via="cold")
            # #3b: the answer may live only in a session SUMMARY (a how-to, a rule, an install command) the
            # extractor never atomized into a fact. Fall back to the most relevant summary before abstaining.
            proc = self._procedural_fallback(query, user, as_of=as_of)
            if proc is not None:
                return proc
            summ = self._summary_fallback(query, user_id, as_of=as_of)
            if summ is not None:
                return summ
            return SearchResult(query=query, facts=facts, scores=scores, via="abstain", abstained=True)

        if via != "cold":
            reinforce(facts[0], self.config.access_boost)
        return SearchResult(query=query, facts=facts, scores=scores, via=via)

    def as_of(self, query: str, when: float, user_id: str = "default", top_k: Optional[int] = None) -> SearchResult:
        """Answer 'what did we believe at time `when`?' -- bi-temporal point-in-time query."""
        return self.search(query, user_id=user_id, as_of=when, top_k=top_k)

    def retrieve_episodes(
        self,
        query: str,
        user_id: str = "default",
        k: int = 5,
        pool: Optional[int] = None,
        as_of: Optional[float] = None,
    ):
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
            self.embedder.embed(query),
            pool,
            where=lambda e: e.user_id == user and (as_of is None or e.event_time <= as_of),
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
        ranked_facts = [f for f, _ in ranked_by_date]
        fact_lines = [f"- [{self._stamp(f)}] {f.text}" for f in ranked_facts]
        facts_block = "\n".join(fact_lines) or "(none)"

        chunk_block = ""
        episodes: list[Episode] = []
        if k_chunks:
            if agentic and self.llm is not None:
                from .retrieve.agentic import AgenticRetriever

                episodes = AgenticRetriever(self, self.llm).gather_episodes(query, user, k_chunks, as_of=as_of)
            else:
                episodes = self.retrieve_episodes(search_query, user, k_chunks, as_of=as_of)
            if self.config.provenance_chunk_promotion:
                promoted = self._provenance_detail_chunks(ranked_facts, search_query, user, as_of, k_chunks)
                if promoted:
                    episodes = self._merge_promoted_chunks(promoted, episodes, k_chunks)
            parts = []
            for ep in episodes:
                date = ep.metadata.get("date") or fmt_date(ep.event_time)
                parts.append(f"[{date}]\n{ep.content}")
            chunk_block = "\n\n".join(parts)

        result = (
            f"FACTS (current, with dates):\n{facts_block}\n\n"
            f"RELEVANT CONVERSATIONS (with dates):\n{chunk_block}"
        ).strip()
        if self.config.chain_evidence:
            evo = self._fact_evolution_block(ranked_facts, user, as_of, query=search_query)
            if evo:
                result += f"\n\n{evo}"
        if self.config.provenance_evidence:
            raw_prov = self._provenance_raw_block(
                ranked_facts,
                search_query,
                user,
                as_of,
                exclude_episode_ids={ep.id for ep in episodes},
            )
            if raw_prov:
                result += f"\n\n{raw_prov}"
        if timeline:
            # explicit chronological ordering of the relevant facts — helps "first/after/how long" (M2b)
            ordered = sorted((f for f, _ in ranked), key=lambda f: f.valid_at)
            tl = "\n".join(f"- [{self._stamp(f, True)}] {f.text}" for f in ordered) or "(none)"
            result = f"TIMELINE (oldest to newest):\n{tl}\n\n" + result
        if graph:
            # L2: traverse the entity graph from the query's anchor entities to pull connected facts
            # across sessions (multi-hop / multi-session).
            related = self._graph_related_facts(search_query, user, as_of)
            if related:
                block = "\n".join(f"- [{self._stamp(f, True)}] {f.text}" for f in related)
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
        more = self.retrieve_episodes(gap.strip(), user, 2, as_of=as_of)
        return "\n\n".join(f"[{ep.metadata.get('date', '?')}]\n{ep.content}" for ep in more)

    def _graph_related_facts(self, query: str, user: str, as_of: Optional[float], limit: int = 8) -> list[Fact]:
        if not self.config.graph_proximity:
            return []
        live = [f for f in self._all_facts() if f.user_id == user and f.is_live(as_of)]
        if not live:
            return []
        graph_scores, _ = self.retriever._graph_scores(query, user, live, as_of)
        related = [f for f in live if graph_scores.get(f.id, 0.0) > 0.0]
        related.sort(key=lambda f: (-graph_scores.get(f.id, 0.0), -f.valid_at, f.text.lower()))
        return related[:limit]

    def _entity_notes(self, query: str, user: str, as_of: Optional[float], max_entities: int = 3) -> list[str]:
        if self.llm is None:
            return []
        notes: list[str] = []
        for eid in list(self.retriever.query_entity_ids(query, user))[:max_entities]:
            ent = self.graph.entities.get(eid)
            if ent is None:
                continue
            facts = [
                f for f in self._all_facts()
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
        return history(self._all_facts(), user, subject, predicate)

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

    def _summary_fallback(
        self,
        query: str,
        user_id: str,
        as_of: Optional[float] = None,
    ) -> Optional[SearchResult]:
        """#3b: when atomized facts can't answer, surface the most relevant session SUMMARY if it genuinely
        overlaps the query (the info may live only in a summary — a how-to, a rule, an install command — that
        the extractor never distilled into a fact). Conservative: requires a non-generic lexical overlap, so
        it never returns a vaguely-similar summary as if it were the answer."""
        if not self.config.summary_fallback:
            return None
        candidates: list[tuple[int, int, float, Episode, str]] = []
        k = max(1, int(getattr(self.config, "summary_fallback_k", 6) or 6))
        for ep in self.retrieve_summaries(query, user_id, k=k, as_of=as_of):
            text = (ep.summary or ep.content or "").strip()
            if not text:
                continue
            exact = overlap_terms(query, text) - _GENERIC_ATTR_TERMS
            if exact:
                candidates.append((len(exact), len(overlap_terms(query, text)), ep.event_time, ep, text))
        if candidates:
            _, _, _, ep, text = max(candidates, key=lambda row: (row[0], row[1], row[2]))
            dated = f"[{ep.metadata.get('date') or fmt_date(ep.event_time)}] (session: {ep.session_id}) {text}"
            return SearchResult(query=query, via="summary", _answer=dated)
        return None

    def _procedural_fallback(
        self,
        query: str,
        user: str,
        as_of: Optional[float] = None,
    ) -> Optional[SearchResult]:
        """Typed fallback for standing instructions/how-to facts.

        Hybrid retrieval may abstain when a procedural fact's predicate is generic ("procedure", "rule")
        and only the object carries the useful steps. Before falling back to free-text summaries, answer
        from the source-backed procedural view so durable rules stay typed and auditable.
        """
        rows = self._procedural_candidates(query, user, as_of, limit=1)
        if not rows:
            return None
        f = rows[0]
        reinforce(f, self.config.access_boost)
        source = self._procedural_source_label(f)
        pred = f.predicate.replace("_", " ")
        answer = f"[{fmt_date(f.valid_at)}] ({source}) {f.subject} {pred}: {f.object}"
        return SearchResult(query=query, facts=[f], scores=[1.0], via="procedural", _answer=answer)
