"""Shared, dependency-free helpers (ids, time, tokenization, vector math)."""
from __future__ import annotations

import math
import re
import time
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


_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")


def date_terms(epoch: float) -> str:
    """Render a fact's date as searchable tokens (year, numeric month, month name) so a query that names
    a time ('May 2023', 'in 2024') matches the right-dated facts via BM25 — dates otherwise live only in
    valid_at and are invisible to retrieval. This is query-time temporal matching done as a lexical signal
    (MemoryScope time_ratio in spirit), with no score multiplier that could override relevance.

    Lives here, in the dependency-free base layer, because BOTH the retriever (which scores) and the
    lexical index (which precomputes corpus statistics) must tokenize a fact identically — otherwise the
    index's global IDF would describe a different corpus than the one being scored."""
    try:
        d = fmt_date(epoch)  # YYYY-MM-DD
        y, m, _ = d.split("-")
        return f"{d} {y} {m} {_MONTHS[int(m) - 1]}"
    except (ValueError, IndexError):
        return ""


def indexed_text(text: str, epoch: float) -> str:
    """The exact string the lexical channel treats as a fact's document. Single source of truth so the
    index and the scorer never drift apart."""
    return f"{text} {date_terms(epoch)}"


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
