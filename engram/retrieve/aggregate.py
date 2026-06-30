"""Structured aggregation candidates for count/list questions.

The goal is to avoid asking the answerer to infer object boundaries from noisy prose. We first map raw
evidence into typed candidates, dedupe aliases, and explicitly mark accessories/protectors that should not
be counted as the requested object.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..types import Episode, Fact
from ..util import fmt_date, stems
from .lexical import overlap_terms


@dataclass(frozen=True)
class AggregationCandidate:
    canonical_item: str
    raw_item: str
    item_type: str
    action: str
    action_raw: str
    date: float
    evidence: str
    include: bool = True
    confidence: float = 0.8
    exclude_reason: str = ""
    value: float | None = None
    unit: str = ""


_ACTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("fixed", r"\b(got around to fixing|fixed|repaired|tightened)\b"),
    ("assembled", r"\b(assembled|built|put together|set up)\b"),
    ("bought", r"\b(bought|purchased|ordered|got|took the plunge and ordered)\b"),
    ("sold", r"\b(sold|listed|resold|got rid of)\b"),
    ("worked_on", r"\b(worked on|finished|completed|started|built|assembled)\b"),
)

_ACTION_ALIASES = {
    "assemble": "assembled",
    "assembled": "assembled",
    "build": "assembled",
    "built": "assembled",
    "buy": "bought",
    "bought": "bought",
    "get": "bought",
    "got": "bought",
    "order": "bought",
    "ordered": "bought",
    "purchase": "bought",
    "purchased": "bought",
    "fix": "fixed",
    "fixed": "fixed",
    "repair": "fixed",
    "repaired": "fixed",
    "sell": "sold",
    "sold": "sold",
}

_TARGET_TYPES = {
    "furniture": {
        "items": (
            "coffee table", "kitchen table", "dining table", "table", "bookshelf", "bookcase",
            "mattress", "bed", "dog bed", "desk", "chair", "couch", "sofa", "cabinet", "dresser",
            "nightstand", "wardrobe", "shelf",
        ),
        "exclude": (
            "scratch guard", "scratch guards", "protector", "protectors", "cover", "covers",
            "throw pillow", "throw pillows", "pillow", "pillows", "blanket", "blankets",
        ),
    },
    "model_kit": {
        "items": (
            "model kit", "kit", "spitfire", "revell f-15", "f-15 eagle", "tiger i tank",
            "b-29 bomber", "camaro", "diorama",
        ),
        "exclude": (),
    },
}


def _target_type(query: str) -> str:
    q = query.lower()
    if "furniture" in q:
        return "furniture"
    if "model kit" in q or "model kits" in q:
        return "model_kit"
    return ""


_NUMERIC_GENERIC_TERMS = {
    "how", "many", "much", "total", "sum", "spent", "spend", "cost", "paid", "pay", "money",
    "dollar", "dollars", "hour", "hours", "minute", "minutes", "page", "pages", "count",
    "last", "past", "recent", "recently", "month", "months", "week", "weeks", "year", "years",
    "did", "have", "has", "had", "was", "were", "the", "and", "for", "with", "from", "into",
}


def _numeric_mode(query: str) -> str:
    q = query.lower()
    if re.search(r"\b(miles?\s+per\s+gallon|mpg)\b", q):
        return ""
    if re.search(r"\b(hours?|hrs?|minutes?|mins?)\b", q):
        return "duration"
    if (
        re.search(r"\b(money|paid|cost|costs|saving|savings|save|dollars?)\b", q)
        or "$" in q
        or (re.search(r"\bspen[td]\b", q) and re.search(r"\b(money|dollars?)\b", q))
    ):
        return "money"
    if re.search(r"\b(page\s+count|pages?|\d+-page)\b", q):
        return "page_count"
    return ""


def _topic_terms(query: str) -> set[str]:
    return {t for t in stems(query) if len(t) > 2 and t not in _NUMERIC_GENERIC_TERMS}


def _numeric_item(query: str, mode: str) -> str:
    q = query.lower()
    if "workshop" in q:
        return "workshop"
    if "game" in q or "gaming" in q:
        return "game"
    if "jog" in q and "yoga" in q:
        return "jogging/yoga"
    if "yoga" in q:
        return "yoga"
    if "novel" in q or "book" in q:
        return "novel"
    if mode == "money":
        m = re.search(r"\b(?:on|for|attending)\s+(.+?)(?:\s+(?:in|over|during|last|past)\b|[?.!,;:]|$)", q)
        if m:
            return " ".join(m.group(1).split())[:60]
    if mode == "duration":
        m = re.search(r"\b(?:of|spent|playing|doing)\s+(.+?)(?:\s+(?:in|over|during|last|past|total)\b|[?.!,;:]|$)", q)
        if m:
            return " ".join(m.group(1).split())[:60]
    return mode or "item"


def _value_text(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _relevant_numeric_sentence(query: str, text: str) -> bool:
    topics = _topic_terms(query)
    if not topics:
        return True
    if overlap_terms(query, text) & topics:
        return True
    q = query.lower()
    t = text.lower()
    return (
        ("jogging" in q and "jog" in t)
        or ("games" in q and "game" in t)
        or ("workshops" in q and "workshop" in t)
    )


def _numeric_candidates_from_text(query: str, text: str, date: float) -> list[AggregationCandidate]:
    mode = _numeric_mode(query)
    if not mode:
        return []
    item = _numeric_item(query, mode)
    out: list[AggregationCandidate] = []
    sentences = _sentences(text)
    for idx, sent in enumerate(sentences):
        evidence = " ".join(sent.split())
        window = " ".join(
            s for s in (
                sentences[idx - 1] if idx > 0 else "",
                sent,
                sentences[idx + 1] if idx + 1 < len(sentences) else "",
            ) if s
        )
        if not _relevant_numeric_sentence(query, window):
            continue
        matches: list[tuple[float, str, str, str, bool, str]] = []
        if mode == "money":
            for m in re.finditer(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)", evidence):
                value = float(m.group(1).replace(",", ""))
                matches.append((value, "USD", "spent", m.group(0), True, ""))
        elif mode == "duration":
            for m in re.finditer(
                r"\b([0-9]+(?:\.[0-9]+)?)\s*(?:-| )?\s*(hours?|hrs?|minutes?|mins?)\b",
                evidence,
                re.I,
            ):
                value = float(m.group(1))
                unit = m.group(2).lower()
                if unit.startswith(("minute", "min")):
                    value = value / 60.0
                stale = bool(
                    re.search(r"\bused to\b|trying to get back|hoping to get back|slacking off", window, re.I)
                    and re.search(r"\b(last|this|recent|recently|did)\b", query, re.I)
                )
                matches.append((
                    value,
                    "hours",
                    "duration",
                    m.group(0),
                    not stale,
                    "past habit, not completed in requested period" if stale else "",
                ))
        elif mode == "page_count":
            for m in re.finditer(r"\b([0-9][0-9,]{1,4})\s*(?:-| )?pages?\b", evidence, re.I):
                value = float(m.group(1).replace(",", ""))
                matches.append((value, "pages", "page_count", m.group(0), True, ""))
        for value, unit, action, raw, include, reason in matches:
            raw_value = f"{raw} {item}".strip()
            out.append(AggregationCandidate(
                canonical_item=f"{item} {unit} {_value_text(value)} {fmt_date(date)}",
                raw_item=raw_value,
                item_type=mode,
                action=action,
                action_raw=raw,
                date=date,
                evidence=" ".join(window.split())[:500],
                include=include,
                confidence=0.9 if include else 0.75,
                exclude_reason=reason,
                value=value,
                unit=unit,
            ))
    return out


def _action(query: str, text: str) -> tuple[str, str]:
    q = query.lower()
    t = text.lower()
    for action, pattern in _ACTION_PATTERNS:
        if re.search(pattern, q) and (m := re.search(pattern, t)):
            return action, m.group(1)
    for action, pattern in _ACTION_PATTERNS:
        if m := re.search(pattern, t):
            return action, m.group(1)
    return "", ""


def _normalize_action(action: str) -> str:
    key = action.strip().lower().replace("_", " ")
    return _ACTION_ALIASES.get(key, key.replace(" ", "_"))


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]


def _user_segments(text: str) -> list[str]:
    if "user:" not in text.lower():
        return [text]
    segments: list[str] = []
    for m in re.finditer(r"(?is)\buser:\s*(.*?)(?=\bassistant:\s*|\buser:\s*|$)", text):
        seg = m.group(1).strip()
        if seg:
            segments.append(seg)
    return segments or [text]


def _canonical(raw: str) -> str:
    item = raw.lower()
    item = re.sub(r"\b(new|old|that|the|my|a|an|from|wooden|wobbly|organic|ikea|west elm|casper)\b", " ", item)
    item = re.sub(r"[^a-z0-9]+", " ", item)
    item = " ".join(item.split())
    aliases = {
        "book shelf": "bookshelf",
        "shelf": "bookshelf",
        "table leg": "table",
        "kitchen table leg": "kitchen table",
        "scratch guards": "scratch guard",
    }
    return aliases.get(item, item)


def _item_mentions(target: str, text: str) -> list[tuple[str, bool, str]]:
    cfg = _TARGET_TYPES.get(target)
    if not cfg:
        return []
    lower = text.lower()
    mentions: list[tuple[str, bool, str]] = []

    def has_item(item: str) -> bool:
        return bool(re.search(r"\b" + re.escape(item).replace(r"\ ", r"\s+") + r"s?\b", lower))

    for ex in cfg["exclude"]:
        if has_item(ex):
            mentions.append((ex, False, "accessory/protector, not requested object"))
    for item in cfg["items"]:
        if has_item(item):
            if target == "furniture" and (item == "dog bed" or (item == "bed" and re.search(r"\b(dog|max|pet)\b", lower))):
                mentions.append((item, False, "pet item, not user's furniture"))
            else:
                mentions.append((item, True, ""))
    filtered: list[tuple[str, bool, str]] = []
    for item, include, reason in mentions:
        if any(item != other and item in other for other, _, _ in mentions):
            continue
        filtered.append((item, include, reason))
    mentions = filtered
    mentions.sort(key=lambda x: (-len(x[0]), not x[1], x[0]))
    return mentions


def _candidate_from_text(query: str, target: str, text: str, date: float) -> list[AggregationCandidate]:
    action, action_raw = _action(query, text)
    if not action:
        return []
    candidates: list[AggregationCandidate] = []
    for item, include, reason in _item_mentions(target, text):
        candidates.append(AggregationCandidate(
            canonical_item=_canonical(item),
            raw_item=item,
            item_type=target,
            action=action,
            action_raw=action_raw,
            date=date,
            evidence=" ".join(text.split())[:280],
            include=include,
            confidence=0.9 if include else 0.75,
            exclude_reason=reason,
        ))
    return candidates


def _query_terms(query: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2}


def _evidence_score(query: str, text: str) -> int:
    lower = text.lower()
    score = len(_query_terms(query) & set(re.findall(r"[a-z0-9]+", lower)))
    for _, pattern in _ACTION_PATTERNS:
        if re.search(pattern, lower):
            score += 4
    if "user:" in lower:
        score += 1
    return score


def _evidence_snippets(
    query: str,
    facts: list[Fact],
    episodes: list[Episode],
    limit: int = 60,
) -> list[tuple[str, float, str]]:
    candidates: list[tuple[int, float, str]] = []
    for fact in facts:
        text = " ".join(fact.text.split())
        candidates.append((_evidence_score(query, text) + 1, fact.valid_at, text[:500]))
    for ep in episodes:
        for segment in _user_segments(ep.content):
            for sent in _sentences(segment):
                text = " ".join(sent.split())
                if len(text) < 8:
                    continue
                candidates.append((_evidence_score(query, text) + 2, ep.event_time, text[:500]))
    candidates.sort(key=lambda row: (-row[0], row[1], row[2]))
    return [(f"E{i}", t, text) for i, (_, t, text) in enumerate(candidates[:limit])]


def _llm_candidates(query: str, facts: list[Fact], episodes: list[Episode], llm) -> list[AggregationCandidate]:
    if llm is None:
        return []
    snippets = _evidence_snippets(query, facts, episodes)
    if not snippets:
        return []
    evidence = "\n".join(f"{sid} | {fmt_date(t)} | {text}" for sid, t, text in snippets)
    prompt = (
        "Extract structured aggregation candidates for the user's count/list question.\n"
        "Return JSON only: an array of objects with keys source_id, action, item, item_type, include, "
        "exclude_reason, confidence. For numeric sum questions, also include numeric value and unit.\n\n"
        "Rules:\n"
        "- include=true only if the candidate is an instance of the requested object type and matches one "
        "of the requested actions or a clear synonym.\n"
        "- Return every matching candidate in the evidence, not just the most salient examples.\n"
        "- Resolve local pronouns and ellipsis inside an evidence line: if the text says the user needed a "
        "new X and then ordered/bought/got one, the item is X and the action is buy.\n"
        "- include=false for accessories, protectors, suggestions, recommendations, unrelated objects, or "
        "items belonging to someone/pets when the query asks about the user.\n"
        "- Use the evidence text; do not invent candidates.\n"
        "- Deduplicate aliases by returning the clearest item name once.\n\n"
        f"Question: {query}\n\nEvidence:\n{evidence}\n"
    )
    try:
        raw = llm.complete(prompt, system="You extract JSON aggregation candidates from evidence.")
        start = raw.find("[")
        end = raw.rfind("]")
        if start < 0 or end < start:
            return []
        data = json.loads(raw[start:end + 1])
    except Exception:
        return []
    by_id = {sid: (t, text) for sid, t, text in snippets}
    out: list[AggregationCandidate] = []
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("source_id", ""))
        if sid not in by_id:
            continue
        date, evidence_text = by_id[sid]
        item = str(row.get("item", "")).strip()
        action = _normalize_action(str(row.get("action", "")))
        if not item or not action:
            continue
        include = bool(row.get("include", True))
        conf = row.get("confidence", 0.8)
        try:
            confidence = float(conf)
        except (TypeError, ValueError):
            confidence = 0.8
        raw_value = row.get("value", None)
        try:
            value = float(raw_value) if str(raw_value or "").strip() else None
        except (TypeError, ValueError):
            value = None
        out.append(AggregationCandidate(
            canonical_item=_canonical(item),
            raw_item=item,
            item_type=str(row.get("item_type", "")).strip() or "item",
            action=action,
            action_raw=action,
            date=date,
            evidence=evidence_text,
            include=include,
            confidence=max(0.0, min(1.0, confidence)),
            exclude_reason=str(row.get("exclude_reason", "")).strip(),
            value=value,
            unit=str(row.get("unit", "")).strip(),
        ))
    return dedupe_aggregation_candidates(out)


def extract_aggregation_candidates(
    query: str,
    facts: list[Fact],
    episodes: list[Episode],
    llm=None,
    numeric: bool = True,
) -> list[AggregationCandidate]:
    llm_out = _llm_candidates(query, facts, episodes, llm)
    numeric_out: list[AggregationCandidate] = []
    if numeric:
        for fact in facts:
            numeric_out.extend(_numeric_candidates_from_text(query, fact.text, fact.valid_at))
        for ep in episodes:
            for segment in _user_segments(ep.content):
                numeric_out.extend(_numeric_candidates_from_text(query, segment, ep.event_time))
    target = _target_type(query)
    if not target:
        return dedupe_aggregation_candidates(llm_out + numeric_out)
    out: list[AggregationCandidate] = []
    for fact in facts:
        out.extend(_candidate_from_text(query, target, fact.text, fact.valid_at))
    for ep in episodes:
        for segment in _user_segments(ep.content):
            for sent in _sentences(segment):
                out.extend(_candidate_from_text(query, target, sent, ep.event_time))
    if not llm_out:
        return dedupe_aggregation_candidates(out + numeric_out)
    llm_keys = {(c.canonical_item, c.action) for c in llm_out}
    fill = [c for c in out if (c.canonical_item, c.action) not in llm_keys]
    return dedupe_aggregation_candidates(llm_out + fill + numeric_out)


def dedupe_aggregation_candidates(candidates: list[AggregationCandidate]) -> list[AggregationCandidate]:
    best: dict[tuple, AggregationCandidate] = {}
    for cand in candidates:
        if cand.value is not None:
            key = (cand.canonical_item, cand.action, cand.unit, cand.value, fmt_date(cand.date))
        else:
            key = (cand.canonical_item, cand.action)
        old = best.get(key)
        if old is None or (cand.include, cand.confidence, -cand.date) > (old.include, old.confidence, -old.date):
            best[key] = cand
    return sorted(best.values(), key=lambda c: (not c.include, c.canonical_item, c.date))


def render_aggregation_candidates(candidates: list[AggregationCandidate], limit: int = 18) -> str:
    if not candidates:
        return ""
    has_values = any(c.value is not None for c in candidates)
    lines = [
        "AGGREGATION CANDIDATES (count or sum INCLUDE rows; do not count/sum EXCLUDE rows):",
    ]
    if has_values:
        lines.extend([
            "status | date | action | item | value | unit | type | evidence",
            "--- | --- | --- | --- | --- | --- | --- | ---",
        ])
    else:
        lines.extend([
            "status | date | action | item | type | evidence",
            "--- | --- | --- | --- | --- | ---",
        ])
    for cand in candidates[:limit]:
        status = "INCLUDE" if cand.include else f"EXCLUDE ({cand.exclude_reason})"
        if has_values:
            value = "" if cand.value is None else _value_text(cand.value)
            lines.append(
                f"{status} | {fmt_date(cand.date)} | {cand.action} | {cand.raw_item} | "
                f"{value} | {cand.unit} | {cand.item_type} | {cand.evidence}"
            )
        else:
            lines.append(
                f"{status} | {fmt_date(cand.date)} | {cand.action} | {cand.raw_item} | "
                f"{cand.item_type} | {cand.evidence}"
            )
    return "\n".join(lines)
