"""Shared, dependency-free helpers (ids, time, tokenization, vector math)."""
from __future__ import annotations

import math
import re
import time
import uuid
from datetime import datetime, timezone

# Epoch seconds; one day in seconds (used by recency decay).
DAY = 86400.0


def fmt_date(epoch: float) -> str:
    """Epoch seconds -> 'YYYY-MM-DD' (UTC). Used to stamp facts/chunks with their real date."""
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")
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
    """Crude singular-ization so work/works and colleague/colleagues align. Shared by the lexical
    scorer AND the offline embedder so semantic and lexical signals agree offline."""
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
