"""The bi-temporal data model. See CLAUDE.md §3.1.

The core invariant: contradicted facts are *invalidated*, never hard-deleted. Every Fact tracks two
independent time axes so we can answer both "what is true now?" and "what did we believe at time T?":

  * valid time      (valid_at / invalid_at)  -- when the fact is true in the world
  * transaction time (created_at / expired_at) -- when *we* learned / retracted it
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .util import gen_id, now


@dataclass
class Episode:
    """A raw, lossless turn or event. Never mutated after ingestion (append-only log)."""

    content: str
    user_id: str = "default"
    session_id: str = "default"
    speaker: str = "user"
    event_time: float = field(default_factory=now)  # when it happened in the world
    ingested_at: float = field(default_factory=now)  # when we recorded it
    embedding: Optional[list[float]] = None
    consolidated: bool = False
    # L2 abstraction: a compact summary of this episode/session, computed once during consolidation and
    # retrieved (instead of the full raw text) to build a LEAN read context — the scaling primitive that
    # lets a small retrieved slice cover many sessions cheaply (CLAUDE.md Bet A/E).
    summary: str = ""
    summary_embedding: Optional[list[float]] = None
    id: str = field(default_factory=lambda: gen_id("ep"))
    metadata: dict = field(default_factory=dict)


@dataclass
class Fact:
    """An atomic (subject, predicate, object) claim with bi-temporal validity + provenance."""

    subject: str
    predicate: str
    object: str
    text: str = ""  # canonical English-predicate rendering, e.g. "Wei works_at Moonshot AI" (embeds/retrieves)
    # native-language one-line phrasing from extraction (e.g. "李雷在字节跳动工作"), shown in the UI. Empty
    # for old facts / offline extraction => the display layer localizes from the predicate instead.
    display: str = ""
    user_id: str = "default"
    embedding: Optional[list[float]] = None

    salience: float = 1.0  # importance; boosted on access, decayed over time
    confidence: float = 1.0
    # provenance class: "extracted" (auto, from conversation) or "user" (manually asserted/edited via the
    # management UI). A user-asserted fact is AUTHORITATIVE — auto-extraction may never silently override it
    # (so a manual correction sticks). This is what makes user-facing memory editing trustworthy.
    source: str = "extracted"
    # classification (feature ⑤): a coarse domain/category and a sensitivity flag (PII/health/finance/...).
    # Simple defaults so they're class attributes — old pickled facts (without these) read the defaults,
    # keeping snapshots backward-compatible. Sensitive facts can be redacted from shared/export contexts.
    category: str = ""
    sensitive: bool = False
    # The source's own wording for WHEN this happened ("the week before", "first weekend of August").
    # valid_at collapses time to one stamp, which is what retrieval and conflict resolution need, but the
    # phrasing is what a temporal question often asks for -- on LOCOMO every granularity failure answered
    # with a stamp where the gold wanted the phrase. Empty for facts whose time was never stated relatively.
    time_phrase: str = ""

    # --- valid time (world) ---
    valid_at: float = field(default_factory=now)
    invalid_at: Optional[float] = None  # None => still true in the world

    # --- transaction time (belief) ---
    created_at: float = field(default_factory=now)
    expired_at: Optional[float] = None  # None => still believed

    supersedes: Optional[str] = None  # id of the fact this one replaced (the evolution chain)
    provenance: list[str] = field(default_factory=list)  # source Episode ids

    last_access: float = field(default_factory=now)
    access_count: int = 0
    id: str = field(default_factory=lambda: gen_id("ft"))

    def __post_init__(self) -> None:
        if not self.text:
            # render the predicate as natural language ("works_at" -> "works at") so embeddings and
            # lexical matching see real text, not snake_case tokens.
            readable = self.predicate.replace("_", " ")
            self.text = f"{self.subject} {readable} {self.object}".strip()

    @property
    def slot(self) -> tuple[str, str, str]:
        """The (user, subject, predicate) key. Two facts sharing a slot are candidate conflicts."""
        return (self.user_id, self.subject.lower(), self.predicate.lower())

    def is_live(self, as_of: Optional[float] = None) -> bool:
        """True if this fact is both currently believed and valid-in-world at `as_of`."""
        t = now() if as_of is None else as_of
        believed = self.expired_at is None or self.expired_at > t
        valid = self.valid_at <= t and (self.invalid_at is None or self.invalid_at > t)
        return believed and valid


@dataclass
class Entity:
    """A node in the semantic graph. Resolved by (user_id, lowercased name) in the reference impl."""

    name: str
    type: str = "entity"
    user_id: str = "default"
    embedding: Optional[list[float]] = None
    aliases: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: gen_id("en"))


@dataclass
class Relation:
    """A directed, bi-temporal edge between two entities, backed by a Fact."""

    subject_id: str
    predicate: str
    object_id: str
    fact_id: str
    valid_at: float = field(default_factory=now)
    invalid_at: Optional[float] = None
    id: str = field(default_factory=lambda: gen_id("rel"))


@dataclass
class WorkingMemory:
    """Short-term WORKING memory (CLAUDE.md §3 typed memory): ephemeral state that must NOT pollute the
    durable long-term store. Bound to a session and/or a hard TTL, and cleared when its session ends, it
    expires, or it is consumed. Examples: "today my throat hurts" (transient state), "remind me to buy
    milk" (intent), seat-occupant facts within one trip. Distinct from `working_set` (the transient query
    attention set) — this is a persisted, lifecycle-managed tier.

    Lifecycle (the general memory-hygiene rule — keep transient context out of long-term):
      * session-scoped  -> lives until clear_session() (e.g. a new conversation / power cycle)
      * ttl/expires_at  -> hard-expires at a wall-clock time (e.g. a dated reminder, a 2h fallback)
      * consumed        -> soft-cleared once it has served its purpose
    """

    content: str
    user_id: str = "default"
    session_id: str = "default"
    kind: str = "state"  # state | intent | schedule | note | passenger | ...
    event_time: float = field(default_factory=now)
    created_at: float = field(default_factory=now)
    expires_at: Optional[float] = None  # hard wall-clock expiry; None => session-scoped only
    consumed: bool = False  # soft-clear once used
    embedding: Optional[list[float]] = None
    id: str = field(default_factory=lambda: gen_id("wm"))
    metadata: dict = field(default_factory=dict)

    def is_live(self, as_of: Optional[float] = None, session_id: Optional[str] = None) -> bool:
        t = now() if as_of is None else as_of
        if self.consumed:
            return False
        if self.expires_at is not None and self.expires_at <= t:
            return False
        if session_id is not None and self.session_id != session_id:
            return False
        return True


@dataclass
class Conflict:
    """A SUSPECTED conflict between two of the user's facts, surfaced for the user to confirm rather than
    auto-resolved (the safe path for the ambiguous tail the cheap rules can't catch: an LLM in System-2
    only DETECTS it; the user decides). `older`/`newer` are by valid time; resolving keeps one and
    supersedes the other. Never auto-applied — no silent corruption."""

    older: str  # fact id with the earlier valid_at (the candidate-outdated one)
    newer: str  # fact id with the later valid_at (the candidate-current one)
    text_older: str = ""  # display snapshots (facts may be edited/deleted later)
    text_newer: str = ""
    user_id: str = "default"
    reason: str = ""
    status: str = "pending"  # pending | resolved | dismissed
    detected_at: float = field(default_factory=now)
    id: str = field(default_factory=lambda: gen_id("cf"))

    @property
    def pair_key(self) -> tuple:
        return tuple(sorted((self.older, self.newer)))
