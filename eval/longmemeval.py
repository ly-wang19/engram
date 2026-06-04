"""LongMemEval runner on Engram's harness (CLAUDE.md §4).

    # end-to-end QA + full-context baseline on the bundled sample (needs an LLM key)
    python eval/longmemeval.py --mode qa --limit 4

    # retrieval Recall@k at scale, no LLM needed (point --data at longmemeval_s.json)
    python eval/longmemeval.py --mode recall --data data/longmemeval_s.json --limit 50

Two complementary measurements:
  * recall : embed every session turn, retrieve top-k, check evidence-session hit. LLM-free, scales to
             the full 500-session haystack. Measures the retrieval half.
  * qa     : ingest -> consolidate (LLM extraction) -> retrieve facts -> LLM answer -> LLM judge, AND a
             full-context baseline (stuff all turns into the prompt) judged identically. Measures the
             whole system and answers the charter's core question: do we beat full-context, and at what
             token cost?
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
import warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

warnings.filterwarnings("ignore")  # quiet litellm/pydantic serialization warnings
logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
os.environ.setdefault("LITELLM_LOG", "CRITICAL")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engram import Memory  # noqa: E402
from engram.llm.providers import load_dotenv, make_embedder, make_llm, make_reranker  # noqa: E402
from engram.util import DAY, fmt_date  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = 1_700_000_000.0

ANSWER_SYSTEM = (
    "You answer the user's question using ONLY the provided context (dated FACTS and dated CONVERSATIONS). "
    "Use the dates to resolve 'current/now', 'first', 'most recent', and durations ('how long'). When facts "
    "conflict, trust the most recent one.\n"
    "If the question asks for a RECOMMENDATION or SUGGESTION, or what the user would PREFER/like: do NOT "
    "refuse. Identify the user's relevant stated preferences, habits, tools, brands, or constraints from the "
    "context, and answer with a specific suggestion that explicitly reflects them (name the preferred "
    "tool/brand/genre/style). Such questions are answerable from the user's preferences even when no literal "
    "answer is stored.\n"
    "For factual questions, answer concisely and specifically. Only when the context genuinely contains no "
    "relevant information, reply exactly: I don't know."
)
ANSWER_TEMPLATE = "Today's date: {qdate}\n\n{context}\n\nQuestion: {question}\nAnswer:"

# Phase 1 — reasoning answer with explicit evidence-aggregation step. Targets the multi-evidence
# failure mode that dominates both-wrong items on _S: counting ('how many sessions'), summing ('how many
# rare items total'), ordering ('order of trips'), date arithmetic ('how old when X'), latest-wins
# (knowledge-update). Single-shot answering doesn't aggregate; this prompts a brief CoT then a final
# canonical "ANSWER: ..." line that the extractor + judge consume.
REASONING_SYSTEM = (
    "You answer the user's question using the provided dated context. The context has two parts: a FACTS "
    "index (a digest) AND the full dated CONVERSATIONS below it. The answer is USUALLY present — search "
    "BOTH parts thoroughly before concluding anything. The dates are real; use them for temporal reasoning.\n\n"
    "DIG FOR THE ANSWER FIRST. Most questions ARE answerable from the history — your job is to find the "
    "evidence, not to give up. If the FACTS digest doesn't contain it, scan the full CONVERSATIONS below.\n\n"
    "WHEN THE QUESTION NEEDS MULTIPLE EVIDENCE PIECES (counting, summing, ordering, date arithmetic, "
    "duration, earliest/latest/most-recent, multi-step lookup, knowledge updated over time):\n"
    "  EVIDENCE: list EVERY relevant dated item from the context, one per line with its date. Be EXHAUSTIVE "
    "— scan the whole history; a missed item makes a count or total wrong. Re-read before finalizing.\n"
    "  REASON: count distinct items / sum / sort by date / compute the date difference / pick the most "
    "recent. Show the work in 1-2 lines. For durations compute end_date − start_date explicitly.\n"
    "  ANSWER: <a single concise final answer — no reasoning on this line>\n\n"
    "FOR DATE/TIME/NUMBER ANSWERS: give the MOST SPECIFIC value the context supports (exact date > "
    "month+year > year; exact duration, not 'a while'). Read the exact figure from the text — don't round "
    "or approximate (e.g. 27 minutes 45 seconds, not 28 minutes).\n\n"
    "WHEN THE QUESTION ASKS FOR A RECOMMENDATION / PREFERENCE / what the user would like or how they'd want "
    "you to respond: do NOT refuse, do NOT ask a clarifying question, do NOT give generic advice. Instead, "
    "ground the answer in the user's OWN stated history: name the SPECIFIC people, places, brands, tools, "
    "past experiences or constraints they mentioned, and tailor the suggestion to those. "
    "Answer 'ANSWER: <concrete suggestion that explicitly references the user's specific stated details>'.\n\n"
    "FOR SIMPLE SINGLE-FACT QUESTIONS: go straight to 'ANSWER: <fact>'.\n\n"
    "CURRENT-STATE questions ('what is my current/latest X', 'where do I work now', anything about the "
    "present): report ONLY the most recent value by date and explicitly disregard older, superseded ones. "
    "The newest dated statement wins; earlier values are history, not the answer.\n\n"
    "When facts conflict, ALWAYS trust the most recent one by date.\n\n"
    "ABSTENTION — a careful LAST resort, only after you have searched the ENTIRE history (facts AND all "
    "conversations) and the SPECIFIC thing asked is genuinely never stated by the user. Do NOT abstain just "
    "because it wasn't in the FACTS digest or wasn't immediately obvious — the answer is usually further "
    "down in the conversations. But if the question presupposes something the user truly never mentioned "
    "(only a different/unrelated topic appears), then reply exactly 'ANSWER: I don't know' rather than "
    "guessing. Never fabricate a value to fill a gap. "
    "The line starting 'ANSWER:' is REQUIRED and must contain the final concise answer only."
)


def extract_answer(pred: str) -> str:
    """Pull the final 'ANSWER: ...' line out of a reasoning response. Falls back to the last non-empty
    line if no marker is present. The judge + abstention check operate on the extracted final answer
    so reasoning text doesn't false-positive the abstention substring match."""
    if not pred:
        return ""
    text = pred.strip()
    # last occurrence of "ANSWER:" (case-insensitive), capture everything after it on that line
    import re as _re

    matches = list(_re.finditer(r"(?i)\bANSWER\s*:\s*(.*)", text))
    if matches:
        tail = matches[-1].group(1).strip()
        # cut at next newline if the model continued (it shouldn't, per prompt)
        tail = tail.split("\n")[0].strip()
        if tail:
            return tail
    # No 'ANSWER:' marker. Reasoning-model backbones (doubao-seed, deepseek) put their chain-of-thought in
    # a separate reasoning_content field and emit the actual answer directly in content — often multi-line
    # (e.g. a personalized recommendation). Returning only the LAST line mangles those into a fragment and
    # the judge never sees the real answer (this silently zeroed the preference category). The official
    # LongMemEval judge grades the FULL response, so when there's no marker, return the whole text.
    return text


# ---- L2 session map + L3 user persona (CLAUDE.md §3 hierarchical abstraction) ----
# Both leading agent-memory systems (Tencent Agent-Memory's L2 scenario-blocks + L3 personas, Volcano
# OpenViking's User-Memory + L0/L1/L2 tiers) lift exactly our two weak categories with these layers:
#   L2 session map  -> multi-session aggregation (count/list/compare without missing a session)
#   L3 user persona -> preference (a pre-built profile to ground recommendations in)
# We build each in ONE LLM pass over the whole dated history (cheap) instead of per-session calls.

SESSION_MAP_SYSTEM = (
    "You condense a long DATED chat history into a COMPLETE session-by-session map, for questions that "
    "aggregate across sessions (counting, listing, comparing, 'how many', 'which ones'). For EVERY dated "
    "conversation, emit one line: '[date] key facts/events/items/decisions/topics in it'. Be COMPLETE and "
    "specific — include every distinct thing the user mentions (names, places, purchases, trips, activities, "
    "numbers), because a later question may need to count or list them. Do NOT merge or skip conversations; "
    "preserve chronological order."
)

PERSONA_SYSTEM = (
    "You build a concise USER PROFILE from a long chat history, to help answer what the user prefers or "
    "would want. Capture WITH SPECIFICS: stated preferences and dislikes; habits and routines; owned "
    "tools/brands/devices; hobbies and interests; skill levels; goals; constraints. Quote concrete details "
    "(names, brands, places, genres). Group into short labeled lines. Include ONLY what the user actually "
    "stated — do not invent."
)


def build_session_map(llm, history_text: str) -> str:
    """L2: one dense pass turning the full dated history into a complete chronological session digest."""
    try:
        out = llm.complete(f"{history_text}\n\nComplete session-by-session map:", system=SESSION_MAP_SYSTEM)
        return out.strip()
    except Exception:  # noqa: BLE001
        return ""


def build_persona(llm, history_text: str) -> str:
    """L3: one pass synthesizing the user's preferences/habits/possessions into a compact profile."""
    try:
        out = llm.complete(f"{history_text}\n\nUSER PROFILE:", system=PERSONA_SYSTEM)
        return out.strip()
    except Exception:  # noqa: BLE001
        return ""


# ---- answer strategies that push the two weak categories (CLAUDE.md Bet B) ----
# Routed by QUESTION TEXT, not the benchmark's category label, so the logic generalizes to real use
# (a deployed system never sees a 'question_type') and isn't gaming the test.

_NUM_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
              "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12"}
_COUNT_CUES = ("how many", "how much", "how often", "number of", "total number", "in total",
               "altogether", "combined", "count of", "how long", "how old", "list all", "all the")
_PREF_CUES = ("recommend", "suggest", "what should i", "what would i", "would i prefer", "help me",
              "advice", "i'm planning", "im planning", "i am planning", "i'm thinking", "what kind of",
              "what type of", "which one should", "any ideas", "what do you suggest")


def needs_self_consistency(question: str) -> bool:
    """Counting / summing / duration / aggregation questions: single-shot answering miscounts at random
    (5->4, 4->6 observed on _S). Sampling K answers and taking the mode cancels the random error."""
    q = question.lower()
    return any(c in q for c in _COUNT_CUES)


def needs_two_stage_pref(question: str) -> bool:
    """Recommendation/preference questions: the model defaults to generic advice. Forcing it to first
    surface the user's concrete stated history, then answer using it, fixes the personalization gap."""
    q = question.lower()
    return any(c in q for c in _PREF_CUES)


def _norm_vote(ans: str) -> str:
    """Normalize an answer for majority voting: lowercase, drop articles/filler, map number-words to
    digits, collapse whitespace. So 'four model kits' and 'I worked on 4 kits' vote together on '4'."""
    import re as _re
    s = ans.lower().strip().strip(".!?\"' ")
    s = _re.sub(r"\b(the|a|an|i|you|your|my|have|has|had|worked|on|or|bought|in|of|total|there|are|is|were|was)\b", " ", s)
    toks = [_NUM_WORDS.get(t, t) for t in _re.split(r"\s+", s) if t]
    # if a number appears, vote on the number alone — it's the decisive content for counting/duration
    nums = [t for t in toks if _re.fullmatch(r"\d+(\.\d+)?", t)]
    if nums:
        return nums[0]
    return " ".join(toks)


def answer_self_consistency(answerer, prompt: str, system: str, k: int = 5) -> str:
    """Sample k answers at temperature, return the ORIGINAL answer whose normalized form is most common.
    Returns the full text (not just the normalized key) so the official judge sees a real answer."""
    from collections import Counter
    cands = []
    for i in range(k):
        raw = answerer.complete(prompt, system=system, temperature=0.0 if i == 0 else 0.7)
        cands.append(extract_answer(raw))
    cands = [c for c in cands if c]
    if not cands:
        return ""
    votes = Counter(_norm_vote(c) for c in cands)
    winner_key, _ = votes.most_common(1)[0]
    # return the first full answer that maps to the winning key (longest, to carry the most context)
    matching = [c for c in cands if _norm_vote(c) == winner_key]
    return max(matching, key=len)


PREF_EXTRACT_SYSTEM = (
    "You read a user's long chat history and surface ONLY what they themselves explicitly stated that is "
    "relevant to the question — their concrete preferences, habits, owned tools/brands, past experiences, "
    "skill level, and constraints. Quote specifics (names, brands, places, dates). 3-6 bullet points. "
    "If they stated nothing relevant, say 'NONE'. Do not invent or generalize."
)


def answer_two_stage_pref(answerer, context: str, question: str, qdate: str, answer_system: str) -> str:
    """Stage 1: pull the user's concrete relevant history. Stage 2: answer, with that history pinned at the
    top of the context so the model must ground its recommendation in it instead of giving generic advice."""
    relevant = answerer.complete(
        f"{context}\n\nQuestion: {question}\n\nThe user's relevant stated details:",
        system=PREF_EXTRACT_SYSTEM, temperature=0.0,
    ).strip()
    pinned = f"THE USER'S RELEVANT STATED HISTORY (ground your answer in these specifics):\n{relevant}\n\n{context}"
    return answerer.complete(
        ANSWER_TEMPLATE.format(qdate=qdate, context=pinned, question=question),
        system=answer_system, temperature=0.0,
    )


# LongMemEval_S haystack averages ~497k chars (≈124k tokens) — well within gemini-2.5-flash/pro 1M window.
# 600k gives margin without truncating any item; old 200k only covered the first 40% (oldest sessions!).
FC_CHAR_BUDGET = 600_000

JUDGE_SYSTEM = "You grade answers. Reply with only 'yes' or 'no'."
# Generic fallback (used only for categories not in the official set). Prefer official_judge_prompt().
JUDGE_TEMPLATE = (
    "Question: {q}\nReference answer: {gold}\nCandidate answer: {pred}\n\n"
    "Does the candidate convey the same key information as the reference? Reply yes or no."
)


def official_judge_prompt(task: str, question: str, answer: str, response: str, abstention: bool = False) -> str:
    """The EXACT LongMemEval official judge prompts (external/LongMemEval/.../evaluate_qa.py).

    We match these verbatim so our numbers are directly comparable to the published leaderboard
    (OMEGA 95.4, Mem0 94.4, Hunyuan 85.2). They are deliberately category-aware and more lenient than a
    naive 'same info?' judge — temporal forgives off-by-one days; knowledge-update accepts an answer that
    also restates old info; preference is rubric-based and needn't hit every point. Using a stricter
    judge would understate our score and make it non-comparable (CLAUDE.md Bet D: be the trustworthy
    scoreboard — that means scoring the way the benchmark defines, not harder)."""
    if abstention:
        return ("I will give you an unanswerable question, an explanation, and a response from a model. "
                "Please answer yes if the model correctly identifies the question as unanswerable. The model "
                "could say that the information is incomplete, or some other information is given but the asked "
                "information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model "
                "correctly identify the question as unanswerable? Answer yes or no only.").format(question, answer, response)
    if task in ("single-session-user", "single-session-assistant", "multi-session"):
        return ("I will give you a question, a correct answer, and a response from a model. Please answer yes "
                "if the response contains the correct answer. Otherwise, answer no. If the response is equivalent "
                "to the correct answer or contains all the intermediate steps to get the correct answer, you should "
                "also answer yes. If the response only contains a subset of the information required by the answer, "
                "answer no. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response "
                "correct? Answer yes or no only.").format(question, answer, response)
    if task == "temporal-reasoning":
        return ("I will give you a question, a correct answer, and a response from a model. Please answer yes if "
                "the response contains the correct answer. Otherwise, answer no. If the response is equivalent to "
                "the correct answer or contains all the intermediate steps to get the correct answer, you should "
                "also answer yes. If the response only contains a subset of the information required by the answer, "
                "answer no. In addition, do not penalize off-by-one errors for the number of days. If the question "
                "asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., "
                "predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: {}"
                "\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no "
                "only.").format(question, answer, response)
    if task == "knowledge-update":
        return ("I will give you a question, a correct answer, and a response from a model. Please answer yes if "
                "the response contains the correct answer. Otherwise, answer no. If the response contains some "
                "previous information along with an updated answer, the response should be considered as correct as "
                "long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel "
                "Response: {}\n\nIs the model response correct? Answer yes or no only.").format(question, answer, response)
    if task == "single-session-preference":
        return ("I will give you a question, a rubric for desired personalized response, and a response from a "
                "model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The "
                "model does not need to reflect all the points in the rubric. The response is correct as long as it "
                "recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\n"
                "Model Response: {}\n\nIs the model response correct? Answer yes or no only.").format(question, answer, response)
    # unknown category → generic
    return JUDGE_TEMPLATE.format(q=question, gold=answer, pred=response)

_ABSTAIN_MARKERS = (
    "don't know", "do not know", "not mentioned", "no information", "cannot find",
    "not sure", "unknown", "not stated", "doesn't mention", "isn't mentioned", "no answer",
)


def is_abstention(item: dict) -> bool:
    return str(item.get("question_id", "")).endswith("_abs") or not str(item.get("answer", "")).strip()


def looks_like_abstention(pred: str) -> bool:
    p = pred.lower()
    return any(m in p for m in _ABSTAIN_MARKERS)


def load_data(path: str | None) -> list[dict]:
    if path is None:
        path = os.path.join(HERE, "longmemeval_sample.json")
    elif path in ("oracle", "s", "m", "longmemeval_oracle", "longmemeval_s", "longmemeval_m"):
        from huggingface_hub import hf_hub_download

        filename = path if path.startswith("longmemeval_") else f"longmemeval_{path}"
        path = hf_hub_download(repo_id="xiaowu0162/longmemeval", filename=filename, repo_type="dataset")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def sessions_of(item: dict):
    sessions = item.get("haystack_sessions", [])
    ids = item.get("haystack_session_ids") or [f"sess_{i}" for i in range(len(sessions))]
    return list(zip(ids, sessions))


def parse_date(s):
    """Parse a LongMemEval date like '2023/05/20 (Sat) 02:21' -> (epoch, 'YYYY-MM-DD'). Real dates are
    essential for temporal-reasoning questions ('first', 'how long', 'most recent')."""
    if not s:
        return None
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    tm = re.search(r"(\d{1,2}):(\d{2})", s)
    hh, mm = (int(tm.group(1)), int(tm.group(2))) if tm else (0, 0)
    try:
        ts = datetime(y, mo, d, hh, mm, tzinfo=timezone.utc).timestamp()
        return ts, f"{y:04d}-{mo:02d}-{d:02d}"
    except ValueError:
        return None


def ingest(mem: Memory, item: dict, user_id: str) -> None:
    # one episode per session (concatenated turns): ~10x fewer extraction calls than per-turn, and
    # makes episode->session mapping 1:1 for recall. A session is a coherent memory unit.
    dates = item.get("haystack_dates") or []
    texts: list[str] = []
    meta: list[tuple] = []
    for idx, (sid, turns) in enumerate(sessions_of(item)):
        parsed = parse_date(dates[idx] if idx < len(dates) else None)
        event_time, dstr = parsed if parsed else (BASE + idx * DAY, None)
        text = "\n".join(
            f"{t.get('role', 'user')}: {t.get('content', '')}" for t in turns if t.get("content")
        )
        if text.strip():
            texts.append(text)
            meta.append((sid, event_time, dstr))
    if not texts:
        return
    # Batch-embed the whole haystack in one model.encode call (the per-session loop otherwise serializes
    # ~50 embeds on the GPU lock and dominates wall-clock at _S scale).
    vecs = mem.embedder.embed_batch(texts)
    for (sid, event_time, dstr), text, vec in zip(meta, texts, vecs):
        ep = mem.add(text, user_id=user_id, session_id=sid, speaker="session",
                     event_time=event_time, embedding=vec)
        ep.metadata["date"] = dstr or fmt_date(event_time)


def all_text(item: dict, reverse: bool = False) -> str:
    """Serialise all sessions to text. With reverse=True, most-recent sessions come first — better for
    knowledge-update questions where the LLM should trust the latest fact. Chronological (default) is
    better for temporal ordering questions. The REASONING_SYSTEM prompt handles both via date labels."""
    dates = item.get("haystack_dates") or []
    sessions_list = list(sessions_of(item))
    dates_list = list(dates) + [None] * max(0, len(sessions_list) - len(dates))
    combined = list(zip(sessions_list, dates_list))
    if reverse:
        combined = list(reversed(combined))
    out = []
    for (sid, turns), date_str in combined:
        parsed = parse_date(date_str)
        out.append(f"=== Conversation on {parsed[1] if parsed else f'session {sid}'} ===")
        for turn in turns:
            out.append(f"[{turn.get('role','user')}] {turn.get('content','')}")
    return "\n".join(out)


def judge_correct(item: dict, pred: str, judge_llm) -> bool:
    """Grade a prediction with the OFFICIAL LongMemEval category-specific judge (so our numbers are
    leaderboard-comparable). We send EVERYTHING to the judge — no pre-filtering — exactly like the official
    evaluate_qa.py. (An earlier 'looks_like_abstention' short-circuit was deflating temporal scores: it
    rejected answers like 'June 2023 (not 100% sure)' on the substring 'not sure', even though June 2023 is
    correct. The judge handles a pure 'I don't know' as wrong on its own.)"""
    abstain = is_abstention(item)
    task = item.get("question_type", "?")
    prompt = official_judge_prompt(task, item["question"], item.get("answer", ""), pred, abstention=abstain)
    verdict = judge_llm.complete(prompt, system=JUDGE_SYSTEM).strip().lower()
    return verdict.startswith("y") or verdict[:5].find("yes") != -1


def run_recall(items, embedder, top_k):
    hit, recall, n = 0, 0.0, 0
    for item in items:
        evidence = set(item.get("answer_session_ids") or [])
        if not evidence:
            continue
        mem = Memory(embedder=embedder)  # no llm: pure retrieval
        ingest(mem, item, item["question_id"])
        qvec = embedder.embed(item["question"])
        results = mem.episodes_vec.search(qvec, top_k)
        retrieved_sessions = {ep.session_id for _, ep in results}
        got = retrieved_sessions & evidence
        hit += 1 if got else 0
        recall += len(got) / len(evidence)
        n += 1
    return {"hit@k": hit / n if n else 0.0, "recall@k": recall / n if n else 0.0, "n": n}


def eval_one(item, embedder, extractor_llm, answerer_llm, judge_llm, top_k, k_chunks, extract_k=0, reranker=None):
    """Evaluate one question. Returns a result dict; never raises (errors are captured)."""
    qid, cat, q = item["question_id"], item.get("question_type", "?"), item["question"]
    try:
        mem = Memory(embedder=embedder, llm=extractor_llm, reranker=reranker)
        ingest(mem, item, qid)
        if extract_k and extract_k > 0:
            # _S scale: retrieve-then-extract. Extracting facts from all ~500 sessions is infeasible, so
            # we retrieve (bi-encoder + reranker) the top extract_k sessions for THIS question and
            # consolidate only those. Bounds LLM extraction cost regardless of haystack size.
            top_eps = mem.retrieve_episodes(q, qid, extract_k)
            mem.engine.consolidate(top_eps)
        else:
            mem.consolidate()

        qdate = item.get("question_date", "")
        t0 = time.perf_counter()
        # HYBRID, date-stamped context via the core API: live facts (conflict-resolved/current) + top-k
        # raw chunks (detail). Dates let the answerer resolve temporal & knowledge-update questions.
        context = mem.context_for(q, user_id=qid, top_k=top_k, k_chunks=k_chunks)
        pred = answerer_llm.complete(ANSWER_TEMPLATE.format(qdate=qdate, context=context, question=q), system=ANSWER_SYSTEM)
        latency = (time.perf_counter() - t0) * 1000.0
        eng_ok = judge_correct(item, pred, judge_llm)

        # full-context baseline — isolated so its failure (e.g. _S context exceeding the window) cannot
        # sink the engram measurement. Capped to the model window (full-context doesn't scale; that's the point).
        fc_ok, fc_tok = None, 0
        try:
            fc_context = all_text(item)[:FC_CHAR_BUDGET]
            fc_pred = answerer_llm.complete(ANSWER_TEMPLATE.format(qdate=qdate, context=fc_context, question=q), system=ANSWER_SYSTEM)
            fc_ok = judge_correct(item, fc_pred, judge_llm)
            fc_tok = len(fc_context.split())
        except Exception:  # noqa: BLE001
            fc_ok, fc_tok = None, 0
        return {"qid": qid, "cat": cat, "eng": eng_ok, "fc": fc_ok, "eng_tok": len(context.split()),
                "fc_tok": fc_tok, "lat": latency, "pred": pred.strip()[:60], "err": None}
    except Exception as e:  # noqa: BLE001 - one bad item must not kill a 500-question run
        return {"qid": qid, "cat": cat, "eng": None, "fc": None, "err": f"{type(e).__name__}: {str(e)[:90]}"}


def run_qa(items, embedder, extractor_llm, answerer_llm, judge_llm, top_k, k_chunks, workers=1, out=None, extract_k=0, reranker=None):
    eng_hits, fc_hits = defaultdict(list), defaultdict(list)
    eng_tokens, fc_tokens, latencies = [], [], []
    errors = 0
    lock = threading.Lock()
    outfh = None
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        outfh = open(out, "w", encoding="utf-8")
    state = {"done": 0}

    def handle(r):
        with lock:
            state["done"] += 1
            n = state["done"]
            if r["err"]:
                nonlocal errors
                errors += 1
                print(f"  [{n}/{len(items)}] {r['qid']:14} ERROR {r['err']}", flush=True)
            else:
                eng_hits[r["cat"]].append(r["eng"])
                eng_tokens.append(r["eng_tok"])
                latencies.append(r["lat"])
                if r["fc"] is not None:  # full-context may be absent on _S (didn't fit)
                    fc_hits[r["cat"]].append(r["fc"])
                    fc_tokens.append(r["fc_tok"])
                if n <= 5 or n % 25 == 0:
                    e = 100.0 * sum(v for vs in eng_hits.values() for v in vs) / max(1, sum(len(v) for v in eng_hits.values()))
                    print(f"  [{n}/{len(items)}] running engram acc={e:.1f}%  (last {r['cat']}: "
                          f"eng={'Y' if r['eng'] else 'n'} fc={'Y' if r['fc'] else 'n'})", flush=True)
            if outfh:
                outfh.write(json.dumps(r, ensure_ascii=False) + "\n")
                outfh.flush()

    args = (embedder, extractor_llm, answerer_llm, judge_llm, top_k, k_chunks, extract_k, reranker)
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(eval_one, it, *args) for it in items]
            for fut in as_completed(futures):
                handle(fut.result())
    else:
        for it in items:
            handle(eval_one(it, *args))
    if outfh:
        outfh.close()
    return eng_hits, fc_hits, eng_tokens, fc_tokens, latencies, errors


def _acc(hits):
    flat = [h for v in hits.values() for h in v]
    return 100.0 * sum(flat) / len(flat) if flat else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None, help="path to longmemeval json (default: bundled sample)")
    ap.add_argument("--mode", choices=["recall", "qa", "both"], default="qa")
    ap.add_argument("--limit", type=int, default=0, help="cap number of items (0 = all)")
    ap.add_argument("--embedder", default="bge-small")
    ap.add_argument("--extractor", default="deepseek")
    ap.add_argument("--answerer", default="deepseek")
    ap.add_argument("--judge", default="qwen-plus")
    ap.add_argument("--topk", type=int, default=10, help="facts retrieved into context")
    ap.add_argument("--chunks", type=int, default=3, help="raw session chunks retrieved into context (0=facts only)")
    ap.add_argument("--workers", type=int, default=1, help="parallel questions (use 6-8 for the full set)")
    ap.add_argument("--out", default=None, help="write per-question results JSONL here (checkpoint)")
    ap.add_argument("--extract-k", type=int, default=0, dest="extract_k",
                    help="_S scale: extract facts only from the top-N retrieved sessions (0=consolidate all)")
    ap.add_argument("--reranker", default="none", help="none | bge-reranker | bge-reranker-large | bge-reranker-v2")
    args = ap.parse_args()

    load_dotenv()
    items = load_data(args.data)
    if args.limit and args.limit < len(items):
        # evenly-spaced sample so a small slice still covers all question types (the file is type-sorted)
        stride = max(1, len(items) // args.limit)
        items = items[::stride][: args.limit]
    print(f"LongMemEval runner: {len(items)} items, mode={args.mode}, embedder={args.embedder}, top_k={args.topk}")
    embedder = make_embedder(args.embedder)

    if args.mode in ("recall", "both"):
        r = run_recall(items, embedder, args.topk)
        print(f"\n== RECALL (LLM-free, n={r['n']}) ==")
        print(f"   hit@{args.topk}    {100*r['hit@k']:.1f}%")
        print(f"   recall@{args.topk} {100*r['recall@k']:.1f}%")

    if args.mode in ("qa", "both"):
        print(f"\n== QA (extractor={args.extractor}, answerer={args.answerer}, judge={args.judge}, "
              f"reranker={args.reranker}, extract_k={args.extract_k}, workers={args.workers}) ==")
        ex_llm = make_llm(args.extractor, max_tokens=512, num_retries=3, timeout=60)
        ans_llm = make_llm(args.answerer, max_tokens=128, num_retries=3, timeout=60)
        jdg_llm = make_llm(args.judge, max_tokens=8, num_retries=3, timeout=60)
        reranker = make_reranker(args.reranker)
        eng_hits, fc_hits, eng_tok, fc_tok, lat, errors = run_qa(
            items, embedder, ex_llm, ans_llm, jdg_llm, args.topk, args.chunks, args.workers, args.out,
            args.extract_k, reranker
        )
        if errors:
            print(f"\n   (note: {errors} item(s) errored and were excluded)")
        cats = sorted(set(eng_hits) | set(fc_hits))
        print("\n   per-category accuracy (engram / full-context):")
        for c in cats:
            e = 100.0 * sum(eng_hits[c]) / len(eng_hits[c]) if eng_hits[c] else 0.0
            f = 100.0 * sum(fc_hits[c]) / len(fc_hits[c]) if fc_hits[c] else 0.0
            print(f"     {c:24} {e:6.1f}% / {f:6.1f}%")
        avg = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
        print("\n   OVERALL")
        print(f"     accuracy        engram {_acc(eng_hits):5.1f}%   full-context {_acc(fc_hits):5.1f}%")
        print(f"     context tokens  engram {avg(eng_tok):6.1f}   full-context {avg(fc_tok):7.1f}   "
              f"({avg(fc_tok)/max(1,avg(eng_tok)):.1f}x leaner)")
        print(f"     latency ms      {avg(lat):.0f} (retrieve+answer)")


if __name__ == "__main__":
    main()
