"""L2 structured profile — a typed, grouped VIEW over the live facts (CLAUDE.md §3, the structured layer).

Design constraints (the "reasonable version" we agreed on):
  * DERIVED, not a separate source of truth. Built read-only from the live fact set, so it inherits
    provenance and can never drift from the bi-temporal store.
  * DISPLAY-ONLY tiering. We split items into `confirmed` vs `tentative` purely for presentation
    (so a shaky one-off guess like "might like jazz" isn't shown as part of the canonical profile).
    This NEVER filters retrieval — search()/lean_context() still see every fact, so recall is untouched.
  * NO invented weights. Each item carries an HONEST evidence descriptor (you set it / mentioned N times),
    not a made-up 0.0–1.0 score. Confidence the user can actually reason about.

Promotion to `confirmed` happens when the evidence is real: the user asserted it, it's an explicit
favorite/allergy, it was reinforced on access, or it was stated across ≥2 independent sessions.
"""
from __future__ import annotations

from ..types import Fact

# predicate -> (canonical field key, human label). Identity / basic-info slots (single-valued).
_BASIC: dict[str, tuple[str, str]] = {
    "name": ("name", "姓名"),
    "age": ("age", "年龄"), "age_range": ("age", "年龄"),
    "gender": ("gender", "性别"), "sex": ("gender", "性别"),
    "occupation": ("occupation", "职业"), "job": ("occupation", "职业"),
    "profession": ("occupation", "职业"), "works_as": ("occupation", "职业"),
    "works_at": ("employer", "工作单位"), "employer": ("employer", "工作单位"),
    "company": ("employer", "工作单位"),
    "lives_in": ("home", "常住地"), "home": ("home", "常住地"),
    "based_in": ("home", "常住地"), "lives_at": ("home", "常住地"),
    "born_in": ("birthplace", "出生地"), "birthplace": ("birthplace", "出生地"),
    "birthday": ("birthday", "生日"), "born_on": ("birthday", "生日"), "birth_date": ("birthday", "生日"),
    "married_to": ("spouse", "配偶"), "spouse": ("spouse", "配偶"),
    "has_child": ("children", "子女"), "has_children": ("children", "子女"),
    "children": ("children", "子女"), "kids": ("children", "子女"),
    "nationality": ("nationality", "国籍"),
    "speaks": ("language", "语言"), "language": ("language", "语言"),
    "education": ("education", "教育"), "studied_at": ("education", "教育"),
    "graduated_from": ("education", "教育"), "degree": ("education", "教育"),
}

_POSITIVE = {"likes", "like", "loves", "love", "enjoys", "enjoy", "prefers", "prefer", "favorite",
             "favourite", "interested_in", "fan_of", "into", "fond_of", "wants", "wishes_for"}
_NEGATIVE = {"dislikes", "dislike", "hates", "hate", "avoids", "avoid", "allergic_to", "allergic",
             "cannot_eat", "cant_eat", "not_into", "disinterested_in"}
_HABIT = {"usually", "often", "routine", "regularly", "habit", "tends_to", "commutes", "frequently",
          "daily", "weekly", "every_day", "every_week", "every_morning"}

# coarse category buckets for grouping preferences (keyword match on predicate + object)
_CATEGORY_KW: list[tuple[str, tuple[str, ...]]] = [
    ("健康禁忌", ("allergic", "allergy", "intoleran")),
    ("音乐", ("music", "song", "artist", "singer", "band", "genre", "playlist")),
    ("影视", ("movie", "film", "show", "tv", "series", "video", "cinema", "drama")),
    ("饮食", ("food", "eat", "cuisine", "dish", "restaurant", "drink", "coffee", "tea", "spicy",
              "seafood", "meal", "cook", "snack", "fruit")),
    ("出行", ("travel", "trip", "destination", "hotel", "route", "drive", "flight", "scenery", "poi")),
    ("运动", ("sport", "exercise", "gym", "run", "fitness", "yoga", "basketball", "football", "hike")),
    ("阅读", ("book", "read", "author", "novel", "podcast", "news")),
]


def _polarity(pred: str) -> str | None:
    p = pred.lower()
    if p in _POSITIVE or p.startswith("favorite") or p.startswith("favourite"):
        return "like"
    if p in _NEGATIVE:
        return "dislike"
    return None


def _is_habit(pred: str) -> bool:
    p = pred.lower()
    return p in _HABIT or any(p.startswith(h + "_") for h in _HABIT)


def _category(pred: str, obj: str) -> str:
    p = pred.lower()
    if p.startswith("favorite_") or p.startswith("favourite_"):
        suffix = p.split("_", 1)[1]
        for cat, kws in _CATEGORY_KW:
            if any(k in suffix for k in kws):
                return cat
    hay = f"{p} {obj.lower()}"
    for cat, kws in _CATEGORY_KW:
        if any(k in hay for k in kws):
            return cat
    return "其他"


def _evidence(f: Fact) -> dict:
    """An HONEST, user-legible confidence signal — not a fabricated numeric weight."""
    if f.source == "user":
        return {"kind": "user", "count": 1}
    n = len(set(f.provenance)) or 1
    if f.access_count > 0 and n < 2:
        return {"kind": "reinforced", "count": f.access_count}
    return {"kind": "mentions", "count": n}


def _confirmed(f: Fact) -> bool:
    """Promote to the canonical profile only on REAL evidence. Display-only — never gates retrieval."""
    p = f.predicate.lower()
    if f.source == "user":
        return True
    if p.startswith("favorite") or p in {"allergic_to", "allergic", "cannot_eat"}:
        return True  # explicit favorite / allergy — stated, not guessed
    if len(set(f.provenance)) >= 2:
        return True  # corroborated across independent sessions
    if f.access_count >= 1:
        return True  # reinforced on access
    return False


def build_structured_profile(facts: list[Fact], subject: str, user_id: str = "default") -> dict:
    """Group the user's live facts into basic info / weighted-free preferences / habits, split into
    confirmed vs tentative for display. `facts` should already be the live set."""
    who = {subject.lower(), "user", user_id.lower(), "i"}
    mine = [f for f in facts if f.subject.lower() in who]

    basic: dict[str, dict] = {}
    prefs: dict[str, list] = {}
    habits: list[dict] = []
    tentative: list[dict] = []

    for f in mine:
        pred = f.predicate.lower()
        # 1) basic identity slots (single-valued: keep the best-evidenced per field)
        if pred in _BASIC:
            field, label = _BASIC[pred]
            cand = {"field": field, "label": label, "value": f.object,
                    "evidence": _evidence(f), "source": f.source, "fact_id": f.id}
            cur = basic.get(field)
            if cur is None or _rank(f) > cur["_rank"]:
                cand["_rank"] = _rank(f)
                basic[field] = cand
            continue
        # 2) preferences (polarity-tagged), grouped by coarse category
        pol = _polarity(pred)
        if pol is not None:
            item = {"item": f.object, "polarity": pol, "category": _category(pred, f.object),
                    "evidence": _evidence(f), "source": f.source, "fact_id": f.id,
                    "subject": f.subject, "predicate": f.predicate, "object": f.object}
            if _confirmed(f):
                prefs.setdefault(item["category"], []).append(item)
            else:
                tentative.append(item)
            continue
        # 3) habits / routines (light)
        if _is_habit(pred):
            habits.append({"text": f.text, "evidence": _evidence(f), "fact_id": f.id})

    for b in basic.values():
        b.pop("_rank", None)
    return {
        "basic": list(basic.values()),
        "preferences": prefs,
        "habits": habits,
        "tentative": tentative,
        "counts": {
            "basic": len(basic),
            "preferences": sum(len(v) for v in prefs.values()),
            "tentative": len(tentative),
            "habits": len(habits),
        },
    }


def _rank(f: Fact) -> tuple:
    """Pick the most trustworthy fact for a single-valued slot: user-asserted first, then most
    corroborated, then most recently valid."""
    return (1 if f.source == "user" else 0, len(set(f.provenance)), f.valid_at)
