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
_DIETARY = {"diet", "dietary_restriction", "dietary_restrictions"}
_NEGATIVE = {"dislikes", "dislike", "hates", "hate", "avoids", "avoid", "allergic_to", "allergic",
             "cannot_eat", "cant_eat", "not_into", "disinterested_in"}
_HABIT = {"usually", "often", "routine", "regularly", "habit", "tends_to", "commutes", "frequently",
          "daily", "weekly", "every_day", "every_week", "every_morning"}

# coarse category buckets for grouping preferences (keyword match on predicate + object)
# keyword -> category, EN + ZH. First match wins, so ORDER matters: a phrase like "有游泳池的酒店"
# contains both 酒店(出行) and 游泳(运动) — putting 出行 before 运动 keeps it under 出行.
_CATEGORY_KW: list[tuple[str, tuple[str, ...]]] = [
    ("健康禁忌", ("allergic", "allergy", "intoleran", "过敏", "忌口", "禁忌")),
    ("饮食", ("food", "eat", "cuisine", "dish", "restaurant", "drink", "coffee", "tea", "spicy",
              "seafood", "meal", "cook", "snack", "fruit",
              "吃", "餐", "菜", "料", "火锅", "辣", "香菜", "甜", "咖啡", "茶", "清淡", "评分", "人均", "口味")),
    ("出行", ("travel", "trip", "destination", "hotel", "route", "drive", "flight", "scenery", "poi",
              "酒店", "住宿", "民宿", "高架", "路线", "停车", "充电", "景点", "旅游", "出差", "导航", "自驾")),
    ("音乐", ("music", "song", "artist", "singer", "band", "genre", "playlist",
              "歌", "音乐", "曲", "电音", "流行", "周杰伦", "五月天", "华语", "粤语", "民谣", "摇滚")),
    ("影视", ("movie", "film", "show", "tv", "series", "video", "cinema", "drama", "director",
              "片", "电影", "电视剧", "影视", "恐怖", "科幻", "诺兰", "导演", "综艺", "动画")),
    ("运动", ("sport", "exercise", "gym", "run", "fitness", "yoga", "basketball", "football", "hike",
              "跑步", "游泳", "健身", "篮球", "足球", "瑜伽", "运动", "球", "爬山", "登山")),
    ("阅读", ("book", "read", "author", "novel", "podcast", "news", "书", "阅读", "新闻", "播客", "股票")),
    ("兴趣", ("跳舞", "探店", "摄影", "自拍", "烘焙", "绘画", "手工")),
    ("环境", ("安静", "氛围", "环境", "嘈杂", "空调", "温度", "座椅", "加热")),
]


_POS_PREFIX = ("favorite", "favourite", "likes_", "loves_", "enjoys_", "prefers_", "prefer_",
               "interested", "fond_", "fan_")
_NEG_PREFIX = ("dislikes_", "dislike_", "hates_", "avoids_", "avoid_", "allergic", "cannot_",
               "doesn't_", "does_not_", "do_not_", "not_", "no_")


def _polarity(pred: str) -> str | None:
    """like / dislike polarity of a preference predicate — handles compound predicates the LLM emits
    (likes_quiet_environments, doesn't_drive_on_elevated_roads, ...) not just the canonical verbs."""
    p = pred.lower()
    if p in _DIETARY:
        return "diet"
    if p in _POSITIVE or p.startswith(_POS_PREFIX):
        return "like"
    if p in _NEGATIVE or p.startswith(_NEG_PREFIX):
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
    """Whether a preference shows in the canonical profile vs sits as a 待确认 candidate (display-only,
    never gates retrieval). An EXPLICITLY STATED preference is confirmed — the user said it, it isn't a
    shaky inference (the PRD treats 主动写入 as confidence 1.0). 待确认 is reserved for genuinely weak
    signals (e.g. a future passive-inference path emitting sub-1.0 confidence)."""
    if f.source == "user":
        return True
    if _polarity(f.predicate) is not None:  # an explicitly stated like / dislike / favorite / allergy
        return True
    if len(set(f.provenance)) >= 2 or f.access_count >= 1:  # corroborated across sessions / reinforced
        return True
    return False


# Universal first-person / user references (EN + ZH coreference). These denote THE USER — not a
# special case but the basic identity-resolution every memory system needs. Facts about other people
# (儿子/外婆/朋友 ...) simply aren't in this set, so they're excluded without any kinship list or guessing.
USER_REFS = {"user", "用户", "i", "我", "me", "myself", "本人", "自己", "俺"}


def user_aliases(subject: str, user_id: str) -> set[str]:
    """Subjects that denote the user: the resolved self-name (e.g. 李雷) + the user_id + USER_REFS.
    Relies on extraction having normalized first-person subjects to the self-name (see LLMExtractor)."""
    return {subject.lower(), user_id.lower()} | USER_REFS


def build_structured_profile(facts: list[Fact], subject: str, user_id: str = "default") -> dict:
    """Group the user's live facts into basic info / weighted-free preferences / habits, split into
    confirmed vs tentative for display. `facts` should already be the live set."""
    who = user_aliases(subject, user_id)
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

    # The user's name is resolved into the identity (self-name) rather than stored as a fact, so surface
    # it in 基本信息 explicitly when we have it (and no name fact already provided one).
    if "name" not in basic and subject and subject.lower() not in (USER_REFS | {user_id.lower()}):
        basic["name"] = {"field": "name", "label": "姓名", "value": subject,
                         "evidence": {"kind": "mentions", "count": 1}, "source": "extracted", "fact_id": ""}
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
