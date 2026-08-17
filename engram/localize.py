"""Display localization (i18n) — render a Fact in the user's language WITHOUT touching the canonical
English predicate (the slot key that makes cross-language conflict resolution work). Data model = canonical;
presentation = localized. This is purely a display layer: retrieval, dedup, and conflict logic are unchanged.

Auto mode localizes a fact to Chinese only when its content actually contains CJK (i.e. the user recorded
in Chinese) — English-recorded facts (e.g. the LongMemEval corpus) are left exactly as they are, so we never
force-translate someone's data.
"""
from __future__ import annotations

# canonical predicate -> Chinese render template. Object-centric statements (subject is implied "你");
# {o} = object. Covers the common predicates; the long tail falls back to a readable rendering.
_ZH: dict[str, str] = {
    "name": "名字是 {o}", "age": "年龄 {o}", "age_range": "年龄段 {o}", "gender": "性别 {o}",
    "occupation": "职业是 {o}", "job": "职业是 {o}", "profession": "职业是 {o}",
    "works_at": "在 {o} 工作", "employer": "在 {o} 工作", "company": "在 {o} 工作",
    "works_on": "在做 {o}", "works_in": "在 {o} 工作",
    "lives_in": "住在 {o}", "home": "家在 {o}", "based_in": "常驻 {o}", "lives_at": "住在 {o}",
    "born_in": "出生于 {o}", "birthplace": "出生于 {o}", "birthday": "生日是 {o}",
    "nationality": "国籍 {o}", "speaks": "会说 {o}", "language": "语言 {o}",
    "education": "教育：{o}", "studied_at": "就读于 {o}", "graduated_from": "毕业于 {o}", "degree": "学位 {o}",
    "married_to": "配偶是 {o}", "spouse": "配偶是 {o}", "partner": "伴侣是 {o}",
    "has_child": "有孩子 {o}", "has_children": "有孩子 {o}", "have_child": "有孩子 {o}",
    "have_children": "有孩子 {o}", "children": "孩子：{o}", "kids": "孩子：{o}",
    "job_title": "职位是 {o}", "title": "职位是 {o}", "position": "职位是 {o}", "role": "职位是 {o}",
    "friends_with": "和 {o} 是朋友", "knows": "认识 {o}", "colleague": "同事 {o}", "boss": "老板是 {o}",
    "likes": "喜欢 {o}", "loves": "很喜欢 {o}", "enjoys": "喜欢 {o}", "prefers": "偏好 {o}",
    "interested_in": "对 {o} 感兴趣", "fan_of": "是 {o} 的粉丝", "fond_of": "喜欢 {o}",
    "dislikes": "不喜欢 {o}", "hates": "讨厌 {o}", "avoids": "避免 {o}",
    "allergic_to": "对 {o} 过敏", "cannot_eat": "不能吃 {o}",
    "owns": "拥有 {o}", "has": "有 {o}", "has_pet": "养了 {o}",
    "visited": "去过 {o}", "traveled_to": "去过 {o}", "been_to": "去过 {o}", "went_to": "去过 {o}",
    "bought": "买了 {o}", "ordered": "点了 {o}", "plans": "计划 {o}", "plans_to": "计划 {o}",
    "wants": "想要 {o}", "needs": "需要 {o}", "goal": "目标：{o}",
    "has_disease": "患有 {o}", "medication": "在用药 {o}", "salary": "薪资 {o}", "income": "收入 {o}",
    "plays": "会 {o}", "watches": "看 {o}", "reads": "读 {o}", "listens_to": "听 {o}",
    "studies": "学 {o}", "learns": "学 {o}", "learning": "在学 {o}", "drives": "开 {o}",
    "practices": "练 {o}", "eats": "吃 {o}", "drinks": "喝 {o}", "cooks": "做 {o}",
    "mentioned": "提到 {o}", "asked": "问过 {o}", "recommends": "推荐 {o}", "considering": "在考虑 {o}",
    "follows": "关注 {o}", "frequents": "常去 {o}", "frequently_visits": "常去 {o}", "visits": "去 {o}",
    "uses": "用 {o}", "uses_video_platform": "用 {o}", "uses_platform": "用 {o}", "enrolled_in": "就读 {o}",
    "had": "做过 {o}", "won": "中了 {o}", "habits": "习惯 {o}", "habit": "习惯 {o}", "wants_to": "想 {o}",
    "going_to": "要去 {o}", "plans_to_travel": "计划去 {o}", "appointment": "预约 {o}",
}

# favorite_<x> -> "最喜欢的<x 的中文>是 {o}"
_FAV_SUFFIX: dict[str, str] = {
    "color": "颜色", "food": "食物", "music": "音乐", "movie": "电影", "film": "电影", "song": "歌曲",
    "drink": "饮品", "sport": "运动", "book": "书", "place": "地方", "animal": "动物", "season": "季节",
    "hobby": "爱好", "city": "城市", "artist": "歌手", "singer": "歌手", "team": "球队", "brand": "品牌",
    "programming_language": "编程语言", "cuisine": "菜系", "fruit": "水果",
}


def has_cjk(s: str) -> bool:
    return any("一" <= c <= "鿿" for c in (s or ""))


_USER_SUBJECTS = {"user", "i", "me", "myself", "我", "你"}


def render_display(subject: str, predicate: str, obj: str, text: str = "", lang: str = "auto") -> str:
    """Localized display string for a fact. `lang="auto"` renders Chinese only when the fact's content has
    CJK; otherwise returns the canonical English `text` untouched."""
    if lang == "auto":
        lang = "zh" if (has_cjk(obj) or has_cjk(subject) or has_cjk(text)) else "en"
    if lang != "zh":
        return text or f"{subject} {predicate.replace('_', ' ')} {obj}".strip()

    p = predicate.lower()
    if p in _ZH:
        body = _ZH[p].format(o=obj)
    elif p.startswith(("favorite_", "favourite_")):
        suffix = p.split("_", 1)[1]
        body = f"最喜欢的{_FAV_SUFFIX.get(suffix, suffix)}是 {obj}"
    elif p.startswith("likes_"):
        body = f"喜欢 {obj}"
    else:
        body = f"{predicate.replace('_', ' ')} {obj}"  # long-tail fallback (still readable)

    if subject.lower() in _USER_SUBJECTS:
        return body
    return f"{subject}{body}"  # non-user subject: prefix it ("Charles 是老板…")


def display_of(fact, lang: str = "auto") -> str:
    # Prefer the extractor's native-language phrasing (recorded in the source language for ANY predicate);
    # fall back to localizing the canonical predicate for old facts / offline extraction.
    native = (getattr(fact, "display", "") or "").strip()
    if native:
        return native
    return render_display(fact.subject, fact.predicate, fact.object, getattr(fact, "text", ""), lang)
