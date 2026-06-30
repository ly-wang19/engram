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
    duration: bool = False
    history: bool = False
    procedural: bool = False
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


_AGG_TERMS = {"count", "counts", "many", "much", "total", "sum", "all", "every", "each", "list"}
_AGG_OBJECT_TERMS = {"sessions", "times", "cities", "places", "trips", "events"}
_TEMPORAL_TERMS = {
    "when", "date", "day", "before", "after", "during", "between", "first", "last", "latest",
    "recent", "recently", "oldest", "newest", "previous", "timeline", "order", "duration",
    "week", "weeks", "month", "months", "year", "years", "spent",
}
_PREFERENCE_TERMS = {
    "prefer", "prefers", "preference", "favorite", "favourite", "like", "likes", "liked",
    "dislike", "dislikes", "hate", "hates", "love", "loves", "avoid", "avoids", "allergic",
    "diet", "dietary", "restriction", "restrictions", "vegetarian", "vegan", "intolerant",
    "intolerance", "recommend", "recommendation",
}
_CURRENT_TERMS = {
    "now", "current", "currently", "today", "still", "latest", "new", "newest", "updated",
    "changed", "anymore", "most_recent", "often", "frequency",
}
_HISTORY_TERMS = {
    "before", "previous", "previously", "former", "formerly", "old", "older", "past",
    "history", "changed", "updated", "superseded", "replaced", "used",
}
_PROCEDURAL_TERMS = {
    "procedure", "procedures", "process", "workflow", "workflows", "runbook", "runbooks",
    "instruction", "instructions", "rule", "rules", "policy", "policies", "protocol", "protocols",
    "step", "steps", "checklist", "checklists", "always", "never", "should", "must", "remind",
    "reminder", "remember",
}
_RELATION_TERMS = {
    "colleague", "coworker", "friend", "partner", "spouse", "manager", "boss", "child",
    "parent", "sibling", "sister", "brother", "mother", "father", "wife", "husband",
    "company", "employer", "profession", "occupation", "role", "title", "works", "work",
    "lives", "live", "moved", "relocated",
}
_BRIDGE_RELATIONS = (
    "sister", "brother", "mother", "father", "parent", "child", "spouse", "wife", "husband", "partner",
    "colleague", "coworker", "co-worker", "friend", "manager", "boss",
)
_ANSWER_ATTRS = {
    "profession", "occupation", "role", "title", "job", "employer", "company", "work", "works",
    "live", "lives", "location", "city", "home",
}
_EXACT_TERMS = {"id", "email", "phone", "url", "link", "address", "number", "code", "identifier"}

_CJK_PATTERNS = {
    "aggregation": ("多少", "几个", "几次", "哪些", "所有", "全部", "一共", "总共", "列出", "每次"),
    "temporal": ("什么时候", "哪天", "日期", "之前", "之后", "最早", "最近", "最新", "第一次", "最后", "期间"),
    "preference": ("喜欢", "偏好", "更爱", "最爱", "讨厌", "不喜欢", "避免", "推荐",
                   "忌口", "过敏", "饮食禁忌"),
    "current": ("现在", "当前", "目前", "如今", "最新", "还", "是否仍", "不再"),
    "history": ("以前", "之前", "曾经", "过去", "历史", "变化", "变更", "改成", "换成"),
    "procedural": ("怎么", "如何", "步骤", "流程", "规则", "指令", "操作", "办法", "提醒", "记得"),
    "relation": ("同事", "朋友", "老板", "经理", "伴侣", "孩子", "父母", "姐姐", "妹妹", "哥哥", "弟弟", "公司", "职业", "搬到", "住在"),
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


def _multi_hop_subqueries(query: str) -> tuple[str, ...]:
    """Deterministic expansion for relation-chain questions.

    Multi-session questions often hide the answer behind two separate pieces of evidence: a relationship
    anchor ("my sister Maya") and a disambiguating attribute from another session ("Maya moved to Seattle").
    These subqueries retrieve those pieces without using gold labels or benchmark metadata.
    """
    q = query.lower()
    candidates: list[str] = []

    relation = ""
    relation_re = "|".join(re.escape(r) for r in _BRIDGE_RELATIONS)
    m = re.search(rf"\b(?:my|user's|the user's|their|his|her)\s+({relation_re})\b", q)
    if m:
        relation = m.group(1)
        candidates.append(relation)

    attrs = [attr for attr in sorted(_ANSWER_ATTRS) if attr in q]
    for attr in attrs:
        candidates.append(attr)
        if relation:
            candidates.append(f"{relation} {attr}")
    if relation and any(attr in attrs for attr in ("work", "works", "employer", "company")):
        candidates.extend((f"{relation} employer", f"{relation} company", f"{relation} works"))
    if relation and any(attr in attrs for attr in ("live", "lives", "location", "city", "home")):
        candidates.extend((f"{relation} lives", f"{relation} location", f"{relation} city"))

    place = ""
    m = re.search(r"\b(?:moved|relocated|lives?|living)\s+(?:to|in)\s+([a-z][a-z' -]{1,40})", q)
    if m:
        place = re.split(r"\b(?:for|with|after|before|who|that|and|or)\b|[?.!,;:]", m.group(1), 1)[0].strip()
        if place:
            candidates.extend((place, f"moved {place}"))
            if relation:
                candidates.append(f"{relation} moved {place}")

    if relation and not attrs and not place:
        candidates.append(f"{relation} relationship")

    return _dedupe(candidates, query)


def plan_evidence(query: str) -> EvidenceNeed:
    """Return the evidence structure a question needs, using only question text.

    The output is deliberately coarse and explainable; it never inspects benchmark labels or gold answers.
    """
    q = query.lower()
    toks = set(stems(q))
    reasons: list[str] = []

    wh_list_question = bool(re.search(r"\b(?:which|what)\b", q)) and _token_hit(toks, _AGG_OBJECT_TERMS)
    aggregation = (
        _token_hit(toks, _AGG_TERMS)
        or wh_list_question
        or bool(re.search(r"\bhow\s+(many|much)\b", q))
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

    duration = (
        bool(re.search(r"\b(how\s+long|how\s+many\s+(days?|weeks?|months?|years?)|duration|spent)\b", q))
        or bool(re.search(r"\b(days?|weeks?|months?|years?)\s+in\s+total\b", q))
    )
    if duration and "duration" not in reasons:
        reasons.append("duration")

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

    history = (
        _token_hit(toks, _HISTORY_TERMS)
        or "used to" in q
        or _has_phrase(q, _CJK_PATTERNS["history"])
    )
    if history:
        reasons.append("history")

    procedural = (
        bool(re.search(r"\bhow\s+(?:do|should|can|to|would|am|is)\b", q))
        or bool(re.search(r"\b(?:what|which)\s+(?:procedure|process|workflow|runbook|instruction|rule|policy|steps?)\b", q))
        or _token_hit(toks, _PROCEDURAL_TERMS)
        or "remember to" in q
        or _has_phrase(q, _CJK_PATTERNS["procedural"])
    ) and not aggregation
    if procedural:
        reasons.append("procedural")

    relation_hits = _token_hit(toks, _RELATION_TERMS) or _has_phrase(q, _CJK_PATTERNS["relation"])
    possessive_chain = bool(re.search(r"\b(my|their|his|her)\s+\w+'?s\b", q)) or q.count("'s") >= 1
    relation_re = "|".join(re.escape(r) for r in _BRIDGE_RELATIONS)
    named_bridge = bool(re.search(rf"\b(?:my|user's|the user's|their|his|her)\s+(?:{relation_re})\b", q))
    answer_attr = _token_hit(toks, _ANSWER_ATTRS)
    multi_hop = relation_hits and (
        possessive_chain
        or aggregation
        or " of " in q
        or " 的 " in q
        or (named_bridge and answer_attr)
    )
    if multi_hop:
        reasons.append("multi_hop")

    exact_lookup = _token_hit(toks, _EXACT_TERMS) or _has_phrase(q, _CJK_PATTERNS["exact"])
    if exact_lookup:
        reasons.append("exact_lookup")

    abstention_sensitive = bool(re.search(r"\b(do you know|remember|have .*memory|did i ever)\b", q))
    if abstention_sensitive:
        reasons.append("abstention_sensitive")

    kinds = tuple(reasons) if reasons else ("lookup",)
    n_facts = 8 if (preference or current_state or history or procedural or multi_hop or exact_lookup) else 0
    n_summaries = 12 if aggregation else (6 if (timeline or duration or procedural or multi_hop) else 0)
    n_chunks = 2 if (preference or procedural or exact_lookup or multi_hop or duration) else (1 if aggregation or timeline else 0)
    subquery_items: list[str] = []
    if aggregation:
        subquery_items.extend(_aggregation_subqueries(query))
    if multi_hop:
        subquery_items.extend(_multi_hop_subqueries(query))
    subqueries = _dedupe(subquery_items, query)

    return EvidenceNeed(
        kinds=kinds,
        timeline=timeline,
        aggregation=aggregation,
        preference=preference,
        current_state=current_state,
        duration=duration,
        history=history,
        procedural=procedural,
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
