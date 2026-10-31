"""Codex as the engine: one turn at a time, a daily cap, a cool-down after a rate limit, the host first —
and a refused turn is an error the caller records, never an empty answer. No Codex is run here."""
from __future__ import annotations

import threading
import time

import pytest

from engram.llm.codex_llm import CodexLLM, CodexUnavailable
from engram.llm.providers import make_llm
from engram.service import MemoryService


class Clock:
    def __init__(self, t: float = 1_793_400_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_runner_gets_prompt_system_and_timeout():
    seen = {}

    def runner(prompt, *, system=None, timeout=None):
        seen.update(prompt=prompt, system=system, timeout=timeout)
        return "  ok  "

    llm = CodexLLM(runner, timeout=42)
    assert llm.complete("hi", system="be brief", max_tokens=5, num_retries=0) == "  ok  "
    assert seen == {"prompt": "hi", "system": "be brief", "timeout": 42.0}


def test_daily_cap_is_persisted_and_resets_next_day(tmp_path):
    clock = Clock()
    state = str(tmp_path / "codex.json")
    llm = CodexLLM(lambda p, **k: "x", daily_cap=2, state_path=state, clock=clock)
    llm.complete("a")
    llm.complete("b")
    with pytest.raises(CodexUnavailable) as exc:
        CodexLLM(lambda p, **k: "x", daily_cap=2, state_path=state, clock=clock).complete("c")
    assert exc.value.reason == "daily_cap"
    assert llm.usage()["used"] == 2
    clock.t += 86400
    assert llm.complete("d") == "x" and llm.usage()["used"] == 1


def test_rate_limit_starts_a_cool_down(tmp_path):
    clock = Clock()

    def limited(prompt, **kw):
        raise RuntimeError("You've hit your usage limit. Try again later.")

    llm = CodexLLM(limited, state_path=str(tmp_path / "s.json"), clock=clock)
    with pytest.raises(CodexUnavailable) as first:
        llm.complete("a")
    assert first.value.reason == "rate_limited"
    ok = CodexLLM(lambda p, **k: "fine", state_path=str(tmp_path / "s.json"), clock=clock)
    with pytest.raises(CodexUnavailable) as second:
        ok.complete("b")
    assert second.value.reason == "cooling_down"
    clock.t += 15 * 60 + 1
    assert ok.complete("c") == "fine"


def test_other_errors_and_empty_replies_raise():
    with pytest.raises(ValueError):
        CodexLLM(lambda p, **k: (_ for _ in ()).throw(ValueError("boom"))).complete("a")
    with pytest.raises(CodexUnavailable) as exc:
        CodexLLM(lambda p, **k: "   ").complete("a")
    assert exc.value.reason == "empty_reply"


def test_waits_for_the_host_then_gives_up():
    clock = Clock()
    busy = iter([True, True, False])
    naps = []
    llm = CodexLLM(lambda p, **k: "ran", busy=lambda: next(busy), clock=clock,
                   sleep=lambda s: (naps.append(s), setattr(clock, "t", clock.t + s)))
    assert llm.complete("a") == "ran" and naps == [2.0, 2.0]
    stuck = CodexLLM(lambda p, **k: "never", busy=lambda: True, busy_wait=5, clock=clock,
                     sleep=lambda s: setattr(clock, "t", clock.t + s))
    with pytest.raises(CodexUnavailable) as exc:
        stuck.complete("a")
    assert exc.value.reason == "host_busy"


def test_one_turn_at_a_time_across_instances():
    running, peak = [0], [0]
    lock = threading.Lock()

    def runner(prompt, **kw):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.02)
        with lock:
            running[0] -= 1
        return "ok"

    a, b = CodexLLM(runner, daily_cap=0), CodexLLM(runner, daily_cap=0)
    threads = [threading.Thread(target=(a if i % 2 else b).complete, args=("p",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1


def test_no_runner_is_a_clear_refusal(monkeypatch):
    monkeypatch.delenv("ENGRAM_CODEX_RUNNER", raising=False)
    with pytest.raises(CodexUnavailable) as exc:
        make_llm("codex").complete("a")
    assert exc.value.reason == "no_runner"


def test_service_runs_session_outcomes_through_a_codex_runner(tmp_path, monkeypatch):
    calls = []

    def runner(prompt, *, system=None, timeout=None):
        calls.append(prompt)
        if "<session>" in prompt:
            return '[{"kind": "decision", "statement": "报价按 12 万元执行，客户已在周三的会上确认", "why": "客户确认"}]'
        return "讨论报价并定为 12 万"

    import engram.llm.codex_llm as codex_llm

    # the host names its runner as module:attr; park this one on an importable module
    monkeypatch.setattr(codex_llm, "_test_runner", runner, raising=False)
    monkeypatch.setenv("ENGRAM_CODEX_RUNNER", "engram.llm.codex_llm:_test_runner")
    monkeypatch.setenv("ENGRAM_NO_DOTENV", "1")
    svc = MemoryService(data_dir=str(tmp_path), embedder_name="hashing", llm_name="codex")
    svc.import_("me", sessions=[{"session_id": "rollout-1", "metadata": {"source": "agent_session"},
                                 "messages": [{"role": "user", "content": "报价怎么定"},
                                              {"role": "assistant", "content": "按 12 万"}]}])
    out = svc.close_session("me", "rollout-1")
    assert out["outcomes_status"] == "completed", out
    assert any("12 万" in f.text for f in svc.get("me").fact_store.values())
    assert len(calls) == 2  # one summary on import, one conclusions pass on close
