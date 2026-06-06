"""LOCOMO -> LongMemEval item adapter (CLAUDE.md Bet D: a second benchmark on the SAME harness).

Converts the public LOCOMO dataset (Maharana et al., ACL 2024; snap-research/locomo, `data/locomo10.json`)
into the exact item shape `eval/bench.py` consumes, so `python eval/bench.py --data locomo ...` runs every
system through the identical answerer + judge as LongMemEval. One LongMemEval item is produced per LOCOMO
QA pair; the "haystack" for that item is the sample's full multi-session conversation.

Get the data: download `locomo10.json` from https://github.com/snap-research/locomo (`data/locomo10.json`)
and place it at `eval/locomo10.json` (or pass an explicit path to `load_locomo`).

Self-test (no dataset, no API keys needed):  python -m eval.locomo
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATHS = [os.path.join(HERE, "locomo10.json"), os.path.join(HERE, "data", "locomo10.json")]

# LOCOMO QA category id -> readable question_type. Category 5 is adversarial (the answer is NOT in the
# conversation), which maps onto LongMemEval's abstention handling: empty gold answer + "_abs" qid suffix.
CATEGORY = {1: "multi-hop", 2: "temporal-reasoning", 3: "open-domain", 4: "single-hop", 5: "adversarial"}
ADVERSARIAL_CAT = 5

# LOCOMO timestamps look like "1:56 pm on 8 May, 2023". Normalise to the "YYYY/MM/DD HH:MM" that
# eval.longmemeval.parse_date understands, so temporal-reasoning questions get real dates (not synthetic).
_DATE_FORMATS = ("%I:%M %p on %d %B, %Y", "%I:%M %p on %d %B %Y", "%H:%M on %d %B, %Y")


def _norm_date(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y/%m/%d %H:%M")
        except ValueError:
            continue
    return s  # leave as-is; parse_date will fall back to a synthetic incremental date


def _turn_text(turn: dict) -> str:
    txt = (turn.get("text") or "").strip()
    cap = (turn.get("blip_caption") or "").strip()  # LOCOMO is multimodal; fold the image caption into text
    if cap:
        txt = (txt + f" (shares a photo: {cap})").strip()
    return txt


def _sessions(conv: dict):
    """Yield (session_key, date_str, turns) in chronological session order (session_1, session_2, ...)."""
    nums = sorted(int(m.group(1)) for k in conv for m in [re.fullmatch(r"session_(\d+)", k)] if m)
    for i in nums:
        yield f"session_{i}", conv.get(f"session_{i}_date_time", ""), conv.get(f"session_{i}", [])


def convert_sample(sample: dict) -> list[dict]:
    """One LOCOMO sample (a conversation + its QA list) -> a list of LongMemEval-shaped items."""
    conv = sample.get("conversation", {})
    sample_id = sample.get("sample_id") or sample.get("id") or "locomo"

    session_ids: list[str] = []
    sessions: list[list[dict]] = []
    dates: list[str] = []
    for skey, date, turns in _sessions(conv):
        msgs = [{"role": t.get("speaker", "user"), "content": _turn_text(t)} for t in turns if _turn_text(t)]
        if not msgs:
            continue
        session_ids.append(skey)
        sessions.append(msgs)
        dates.append(_norm_date(date))
    qdate = dates[-1] if dates else ""  # question is "asked" as of the most recent session

    items: list[dict] = []
    for j, qa in enumerate(sample.get("qa", [])):
        cat = qa.get("category")
        adversarial = cat == ADVERSARIAL_CAT
        answer = "" if adversarial else str(qa.get("answer", qa.get("adversarial_answer", "")))
        qid = f"{sample_id}_q{j}" + ("_abs" if adversarial else "")
        items.append({
            "question_id": qid,
            "question": qa.get("question", ""),
            "answer": answer,
            "question_type": CATEGORY.get(cat, str(cat)),
            "question_date": qdate,
            "haystack_session_ids": session_ids,
            "haystack_sessions": sessions,
            "haystack_dates": dates,
        })
    return items


def load_locomo(path: str | None = None) -> list[dict]:
    """Load locomo10.json and convert every sample's QA into LongMemEval items."""
    if path is None:
        path = next((p for p in DEFAULT_PATHS if os.path.exists(p)), None)
    if not path or not os.path.exists(path):
        raise FileNotFoundError(
            "LOCOMO data not found. Download locomo10.json from https://github.com/snap-research/locomo "
            f"(data/locomo10.json) and place it at {DEFAULT_PATHS[0]}, or pass an explicit path."
        )
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    items: list[dict] = []
    for sample in raw:
        items.extend(convert_sample(sample))
    return items


def _selftest() -> None:
    """Validate the conversion offline against a synthetic LOCOMO-shaped sample (no dataset / no keys)."""
    sample = {
        "sample_id": "convX",
        "conversation": {
            "speaker_a": "Ann", "speaker_b": "Bob",
            "session_1_date_time": "1:56 pm on 8 May, 2023",
            "session_1": [
                {"speaker": "Ann", "dia_id": "D1:1", "text": "I adopted a dog named Rex."},
                {"speaker": "Bob", "dia_id": "D1:2", "text": "Nice!", "blip_caption": "a brown dog"},
            ],
            "session_2_date_time": "2:30 pm on 15 June, 2023",
            "session_2": [
                {"speaker": "Ann", "dia_id": "D2:1", "text": "Rex learned to sit."},
            ],
        },
        "qa": [
            {"question": "What is Ann's dog's name?", "answer": "Rex", "category": 4, "evidence": ["D1:1"]},
            {"question": "When did Ann adopt Rex?", "answer": "8 May 2023", "category": 2},
            {"question": "What car does Ann drive?", "adversarial_answer": "Not mentioned", "category": 5},
        ],
    }
    items = convert_sample(sample)
    assert len(items) == 3, len(items)
    a, t, adv = items
    # single-hop item
    assert a["question_id"] == "convX_q0" and a["question_type"] == "single-hop" and a["answer"] == "Rex"
    assert a["haystack_session_ids"] == ["session_1", "session_2"]
    assert a["haystack_sessions"][0][0] == {"role": "Ann", "content": "I adopted a dog named Rex."}
    assert "brown dog" in a["haystack_sessions"][0][1]["content"]            # blip caption folded in
    assert a["haystack_dates"][0] == "2023/05/08 13:56"                       # date normalised
    assert a["question_date"] == "2023/06/15 14:30"                          # most-recent session date
    # temporal item
    assert t["question_type"] == "temporal-reasoning"
    # adversarial -> abstention
    assert adv["question_id"].endswith("_abs") and adv["answer"] == "" and adv["question_type"] == "adversarial"
    # compatibility with the harness's own helpers
    from eval.longmemeval import is_abstention, parse_date, sessions_of
    assert parse_date(a["haystack_dates"][0])[1] == "2023-05-08"
    assert is_abstention(adv) is True and is_abstention(a) is False
    assert len(sessions_of(a)) == 2
    print("eval/locomo.py self-test OK: 1 sample ->", len(items), "items; dates + abstention + shape verified")


if __name__ == "__main__":
    _selftest()
