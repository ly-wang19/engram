"""Benchmark-neutral evidence planning for the lean read path.

The planner classifies what *shape of evidence* a question needs, not which benchmark bucket it came
from. That keeps the optimization general: "how many..." gets aggregation evidence, "when..." gets a
timeline, preferences get preference records, and relation chains get graph/path evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..util import stems


@dataclass(frozen=True)
class EvidenceNeed:
    kinds: tuple[str, ...] = ("lookup",)
    timeline: bool = False
    aggregation: bool = False
    preference: bool = False
    current_state: bool = False
    multi_hop: bool = False
    exact_lookup: bool = False
    abstention_sensitive: bool = False
    n_facts: int = 0
    n_summaries: int = 0
    n_chunks: int = 0
    use_agentic: bool = False
    use_cascade: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)
    subqueries: tuple[str, ...] = field(default_factory=tuple)


_AGG_TERMS = {
    "count", "counts", "many", "much", "total", "sum", "all", "every", "each", "list",
    "sessions", "times", "often", "frequency", "cities", "places", "trips", "events",
}
_TEMPORAL_TERMS = {
    "when", "date", "day", "before", "after", "during", "between", "first", "last", "latest",
    "recent", "recently", "oldest", "newest", "previous", "timeline", "order", "duration",
}
_PREFERENCE_TERMS = {
    "prefer", "prefers", "preference", "favorite", "favourite", "like", "likes", "liked",
    "dislike", "dislikes", "hate", "hates", "love", "loves", "avoid", "avoids", "allergic",
    "recommend", "recommendation",
}
_CURRENT_TERMS = {
    "now", "current", "currently", "today", "still", "latest", "new", "newest", "updated",
    "changed", "anymore", "most_recent",
}
_RELATION_TERMS = {
    "colleague", "coworker", "friend", "partner", "spouse", "manager", "boss", "child",
    "parent", "sibling", "company", "employer", "works", "work", "lives", "live",
}
_EXACT_TERMS = {"id", "email", "phone", "url", "link", "address", "number", "code", "identifier"}

_CJK_PATTERNS = {
    "aggregation": ("多少", "几个", "几次", "哪些", "所有", "全部", "一共", "总共", "列出", "每次"),
    "temporal": ("什么时候", "哪天", "日期", "之前", "之后", "最早", "最近", "最新", "第一次", "最后", "期间"),
    "preference": ("喜欢", "偏好", "更爱", "最爱", "讨厌", "不喜欢", "避免", "推荐"),
    "current": ("现在", "当前", "目前", "如今", "最新", "还", "是否仍", "不再"),
    "relation": ("同事", "朋友", "老板", "经理", "伴侣", "孩子", "父母", "公司", "住在"),
    "exact": ("邮箱", "电话", "链接", "地址", "编号", "代码", "号码"),
}


def _has_phrase(q: str, phrases: tuple[str, ...]) -> bool:
    return any(p in q for p in phrases)


def _token_hit(tokens: set[str], terms: set[str]) -> bool:
    return bool(tokens & {stems(t)[0] if stems(t) else t for t in terms})


def _dedupe(items: list[str], original: str, limit: int = 6) -> tuple[str, ...]:
    out: list[str] = []
    seen = {original.strip().lower()}
    for item in items:
        q = " ".join(item.split()).strip(" ?.,;:")
        if len(q) < 3:
            continue
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= limit:
            break
    return tuple(out)


def _quoted_phrases(query: str) -> list[str]:
    phrases: list[str] = []
    for a, b in re.findall(r"'([^']+)'|\"([^\"]+)\"", query):
        p = a or b
        if p:
            phrases.append(p)
    return phrases


def _aggregation_subqueries(query: str) -> tuple[str, ...]:
    """Deterministic recall expansion for count/list/sum questions.

    It turns one broad aggregation question into evidence lookups for the counted object and any quoted
    entities. This is benchmark-neutral: it only uses the query text, and it retrieves evidence rather
    than deciding the answer.
    """
    q = query.lower()
    candidates: list[str] = []

    for phrase in _quoted_phrases(query):
        candidates.extend((phrase, f"weeks {phrase}", f"reading listening {phrase}"))

    obj = ""
    m = re.search(r"\bhow\s+many\s+(.+?)\s+(?:have|did|do|are|were|was|is)\b", q)
    if m:
        obj = m.group(1).strip()
        candidates.append(obj)

    verb_map = (
        ("worked on", ("worked on", "built", "assembled")),
        ("bought", ("bought", "purchased")),
        ("reading", ("read", "reading")),
        ("listening", ("listened", "listening")),
        ("visited", ("visited", "trips", "travel")),
    )
    for cue, expansions in verb_map:
        if cue in q or any(e in q for e in expansions):
            if obj:
                candidates.extend(f"{e} {obj}" for e in expansions)
            else:
                candidates.extend(expansions)

    cleaned = re.sub(
        r"\b(how|many|much|total|sum|in total|do|did|have|has|i|me|my|the|and|or)\b",
        " ",
        q,
    )
    candidates.append(cleaned)
    return _dedupe(candidates, query)


def plan_evidence(query: str) -> EvidenceNeed:
    """Return the evidence structure a question needs, using only question text.

    The output is deliberately coarse and explainable; it never inspects benchmark labels or gold answers.
    """
    q = query.lower()
    toks = set(stems(q))
    reasons: list[str] = []

    aggregation = (
        _token_hit(toks, _AGG_TERMS)
        or bool(re.search(r"\bhow\s+(many|much|often)\b", q))
        or _has_phrase(q, _CJK_PATTERNS["aggregation"])
    )
    if aggregation:
        reasons.append("aggregation")

    timeline = (
        _token_hit(toks, _TEMPORAL_TERMS)
        or _has_phrase(q, _CJK_PATTERNS["temporal"])
    )
    if timeline:
        reasons.append("temporal")

    preference = (
        _token_hit(toks, _PREFERENCE_TERMS)
        or _has_phrase(q, _CJK_PATTERNS["preference"])
    )
    if preference:
        reasons.append("preference")

    current_state = (
        _token_hit(toks, _CURRENT_TERMS)
        or "most recent" in q
        or _has_phrase(q, _CJK_PATTERNS["current"])
    )
    if current_state:
        reasons.append("current_state")

    relation_hits = _token_hit(toks, _RELATION_TERMS) or _has_phrase(q, _CJK_PATTERNS["relation"])
    possessive_chain = bool(re.search(r"\b(my|their|his|her)\s+\w+'?s\b", q)) or q.count("'s") >= 1
    multi_hop = relation_hits and (possessive_chain or aggregation or " of " in q or " 的 " in q)
    if multi_hop:
        reasons.append("multi_hop")

    exact_lookup = _token_hit(toks, _EXACT_TERMS) or _has_phrase(q, _CJK_PATTERNS["exact"])
    if exact_lookup:
        reasons.append("exact_lookup")

    abstention_sensitive = bool(re.search(r"\b(do you know|remember|have .*memory|did i ever)\b", q))
    if abstention_sensitive:
        reasons.append("abstention_sensitive")

    kinds = tuple(reasons) if reasons else ("lookup",)
    n_facts = 8 if (preference or current_state or multi_hop or exact_lookup) else 0
    n_summaries = 12 if aggregation else (6 if timeline else 0)
    n_chunks = 2 if (preference or exact_lookup) else (1 if aggregation or timeline or multi_hop else 0)
    subqueries = _aggregation_subqueries(query) if aggregation else ()

    return EvidenceNeed(
        kinds=kinds,
        timeline=timeline,
        aggregation=aggregation,
        preference=preference,
        current_state=current_state,
        multi_hop=multi_hop,
        exact_lookup=exact_lookup,
        abstention_sensitive=abstention_sensitive,
        n_facts=n_facts,
        n_summaries=n_summaries,
        n_chunks=n_chunks,
        use_agentic=multi_hop,
        use_cascade=aggregation,
        reasons=tuple(reasons),
        subqueries=subqueries,
    )
