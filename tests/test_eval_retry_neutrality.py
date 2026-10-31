"""A retry must not know whether the benchmark expects abstention."""
from eval.bench import Rig, eval_item


class Answers:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def complete(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return next(self.replies)


class Contexts:
    name = "engram_lean"

    def __init__(self):
        self.expansions = []

    def context(self, item, expand=0):
        self.expansions.append(expand)
        return "No relevant evidence." if not expand else "Still no relevant evidence."


def evaluate(monkeypatch, qid, replies, enabled=True):
    monkeypatch.setattr("eval.bench.judge_correct", lambda item, pred, judge: pred == "I don't know")
    answerer, context = Answers(replies), Contexts()
    rig = Rig(None, None, answerer, None, verify_retry=enabled)
    item = {"question_id": qid, "question": "Where is the missing package?", "answer": "unknown"}
    result = eval_item(item, [context], rig)["sys"][context.name]
    assert result["err"] is None
    return result, answerer.calls, context.expansions


def test_answerability_label_cannot_change_retry_decision(monkeypatch):
    answerable = evaluate(monkeypatch, "case", ["I don't know", "The depot"])
    unanswerable = evaluate(monkeypatch, "case_abs", ["I don't know", "The depot"])
    assert answerable[1:] == unanswerable[1:]
    assert answerable[2] == [0, 1]
    assert unanswerable[0]["retry_attempted"] is True
    # A retry that guesses on an unanswerable item must be exposed to the judge, not oracle-skipped.
    assert unanswerable[0]["ok"] is False


def test_retry_still_abstaining_preserves_first_answer_and_context(monkeypatch):
    result, calls, expansions = evaluate(monkeypatch, "case_abs", ["I don't know", "I don't know"])
    assert result["pred"] == "I don't know"
    assert result["ok"] is True
    assert len(calls) == 2
    assert expansions == [0, 1]
    assert result["tok"] == 3
    assert result["tok_unit"] == "whitespace_words"
    assert result["total_tokens"] is None


def test_retry_disabled_or_answered_does_not_expand(monkeypatch):
    for enabled, answer in [(False, "I don't know"), (True, "The depot")]:
        result, calls, expansions = evaluate(monkeypatch, "case", [answer], enabled)
        assert len(calls) == 1
        assert expansions == [0]
        assert result["retry_attempted"] is False


def test_report_does_not_call_words_tokens_or_average_mixed_units():
    from eval.report import format_bench_report

    def row(qid, unit):
        value = {"ok": True, "tok": 100, "lat": 1, "tok_unit": unit}
        return {"qid": qid, "cat": "test", "sys": {"engram_lean": value}}

    word_report = format_bench_report("new.jsonl", [row("a", "whitespace_words")])
    assert "whitespace_words" in word_report
    assert "avg context tokens" not in word_report
    mixed = format_bench_report("mixed.jsonl", [row("a", "whitespace_words"), row("b", "tokens")])
    assert "mixed/unknown" in mixed
    size_row = next(line for line in mixed.splitlines() if "avg context size" in line)
    assert size_row.rstrip().endswith("-")
