"""Shared, dependency-free helpers (ids, time, tokenization, vector math)."""
from __future__ import annotations

import math
import re
import time
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone

# Epoch seconds; one day in seconds (used by recency decay).
DAY = 86400.0

# Console display timezone for fmt_datetime (UTC+8). The eval/LLM-context formatters stay UTC (fmt_date).
BEIJING = timezone(timedelta(hours=8))


def fmt_date(epoch: float) -> str:
    """Epoch seconds -> 'YYYY-MM-DD' (UTC). Used to stamp facts/chunks with their real date."""
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return "?"


def fmt_datetime(epoch: float) -> str:
    """Epoch seconds -> 'YYYY-MM-DD HH:MM:SS' in Beijing time (UTC+8). Console display only: surfaces the
    time-of-day that fmt_date() drops. Facts inherit their episode's event_time, so the clock is real —
    but note LongMemEval session stamps carry only minute precision, so their seconds read ':00'; live
    user-entered facts have true seconds. The LLM-context builders + eval deliberately keep fmt_date: they
    don't need the clock and depend on stable UTC date-only stamps."""
    try:
        return datetime.fromtimestamp(epoch, tz=BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "?"


def now() -> float:
    """Current wall-clock time in epoch seconds."""
    return time.time()


def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def stem(token: str) -> str:
    """Crude singular-ization so work/works, study/studies, and colleague/colleagues align. Shared by
    the lexical scorer AND the offline embedder so semantic and lexical signals agree offline.

    Two rules, both length-gated to spare short base forms (dies/lies/ties stay 'die'/'lie'/'tie' after
    the -s strip, since their base isn't a -y word):
      - length > 4 and ends with 'ies' -> 'y'  (studies -> study, cities -> city, carries -> carry)
      - length > 3 and ends with 's'   -> ''    (works -> work, colleagues -> colleague)
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def stems(text: str) -> list[str]:
    return [stem(t) for t in tokenize(text)]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Safe for non-normalized vectors."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def recency(delta_seconds: float, tau_days: float) -> float:
    """Exponential recency decay in [0, 1]: 1.0 now, -> 0 as the memory ages."""
    if tau_days <= 0:
        return 1.0
    return math.exp(-max(delta_seconds, 0.0) / (tau_days * DAY))


def minmax_normalize(scores: dict[str, float]) -> dict[str, float]:
    """Scale a dict of scores into [0, 1]. Flat input -> all 1.0."""
    if not scores:
        return {}
    lo = min(scores.values())
    hi = max(scores.values())
    if hi - lo < 1e-12:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


# --- graph entity normalization (Bet B: multi-hop walks need clean, convergent nodes) ---

# Names may open with a quote/bracket ("《AI项目经理资料库》"); anything else non-word leading
# (✓, •, markdown bullets) marks UI noise, not an entity.
_ENTITY_OPENERS = "\"'“”‘’《〈【[(「『"
_CLAUSE_PUNCT = re.compile(r"[，。；！？!?;:：]")


def canon_entity_name(name: str) -> str:
    """Canonical graph-node key for an entity surface form.

    Surface variants of one name ("Engram-Memory", "engram memory", "engram_memory", full-width
    forms) must resolve to ONE node, or the edges that should converge on it scatter across
    duplicates and graph proximity / n-hop expansion loses signal. Deliberately conservative:
    only case / unicode-width / separator variants are unified — semantic aliasing ("Engram" vs
    "开源记忆引擎") is NOT attempted here, because a false merge corrupts the graph while a missed
    merge only weakens it.
    """
    s = unicodedata.normalize("NFKC", name or "").strip().lower()
    s = re.sub(r"[\s_\-·./]+", " ", s).strip()
    return s


def entity_worthy(name: str) -> bool:
    """Gate for what may become a graph NODE. Facts that fail this still exist and retrieve fine
    (vector + BM25) — they just don't mint an entity, because sentence-length claims can never be
    referenced twice under the same surface form: they become permanent orphan nodes that n-hop
    walks can neither reach nor leave.
    """
    s = (name or "").strip()
    if not s:
        return False
    first = s[0]
    if not (first.isalnum() or first in _ENTITY_OPENERS or "一" <= first <= "鿿"):
        return False  # leading symbol (✓, •, →) => rendering noise, not a name
    if _CLAUSE_PUNCT.search(s):
        return False  # clause punctuation => a claim/sentence, not a name
    if len(s) > 40:
        return False  # sentence-length (esp. CJK, where 40 chars is a full clause)
    if len(s.split()) > 8:
        return False  # long multi-word phrase => descriptive claim
    return True
