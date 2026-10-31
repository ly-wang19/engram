"""Codex as Engram's model: every completion is one Codex turn, run by the app that hosts Engram.

Engram does not launch Codex itself. A host that already runs Codex safely — 如我 starts restricted,
ephemeral, read-only `codex app-server` turns with its own client identity and Windows path discovery —
hands Engram a runner: a callable `(prompt, *, system, timeout) -> str`, named by
`ENGRAM_CODEX_RUNNER="package.module:attr"`. Duplicating that launch logic here would drift from it.

Codex shares the user's account with everything else they run, so this side adds what a background
memory engine owes that account:

- one turn at a time across the whole process (extractor, summarizer, answerer share one gate);
- a daily cap (`ENGRAM_CODEX_DAILY_CAP`, default 60 turns), persisted in `ENGRAM_CODEX_STATE`;
- a 15-minute cool-down after a usage/rate-limit error instead of retrying into it;
- an optional host busy check (`ENGRAM_CODEX_BUSY="module:attr"`, a callable returning True while the
  host's own work is running): Engram waits for it, and gives up the turn if it stays busy.

Every refusal raises `CodexUnavailable`, never returns "", so callers record a failure instead of an
empty summary or an empty set of conclusions.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from .base import LLM

Runner = Callable[..., str]

DEFAULT_DAILY_CAP = 60
COOLDOWN_SECONDS = 15 * 60
BUSY_WAIT_SECONDS = 10 * 60
DEFAULT_TIMEOUT = 300.0

_RATE_LIMIT = re.compile(r"usage.?limit|rate.?limit|too many requests|\b429\b|quota", re.IGNORECASE)

# One gate per process: the roles may hold different CodexLLM objects but spend one account.
_GATE = threading.Lock()
_STATE_LOCK = threading.Lock()


class CodexUnavailable(RuntimeError):
    """Engram did not run this turn (no runner, daily cap, cool-down, host busy, empty reply)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


def load_callable(spec: str) -> Callable:
    """`package.module:attr` -> the attribute."""
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ValueError(f"expected 'module:attr', got {spec!r}")
    return getattr(importlib.import_module(module_name), attr)


class CodexLLM(LLM):
    model = "codex"

    def __init__(self, runner: Optional[Runner] = None, *, daily_cap: Optional[int] = None,
                 state_path: Optional[str] = None, busy: Optional[Callable[[], bool]] = None,
                 busy_wait: float = BUSY_WAIT_SECONDS, cooldown: float = COOLDOWN_SECONDS,
                 timeout: float = DEFAULT_TIMEOUT, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        spec = os.environ.get("ENGRAM_CODEX_RUNNER", "")
        self._runner = runner
        self._runner_spec = spec
        cap = daily_cap if daily_cap is not None else os.environ.get("ENGRAM_CODEX_DAILY_CAP")
        self.daily_cap = int(cap) if cap not in (None, "") else DEFAULT_DAILY_CAP
        self.state_path = state_path if state_path is not None else os.environ.get("ENGRAM_CODEX_STATE")
        busy_spec = os.environ.get("ENGRAM_CODEX_BUSY", "")
        self._busy = busy if busy is not None else (load_callable(busy_spec) if busy_spec else None)
        self.busy_wait = busy_wait
        self.cooldown = cooldown
        self.timeout = timeout
        self._clock = clock
        self._sleep = sleep
        self._mem_state: dict = {}

    # --- runner ---------------------------------------------------------------------------------------
    def _resolve_runner(self) -> Runner:
        if self._runner is None:
            if not self._runner_spec:
                raise CodexUnavailable("no_runner", "set ENGRAM_CODEX_RUNNER to the host's turn runner")
            self._runner = load_callable(self._runner_spec)
        return self._runner

    # --- persisted day counter + cool-down ------------------------------------------------------------
    def _read_state(self) -> dict:
        if not self.state_path:
            return dict(self._mem_state)
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                state = json.load(fh)
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, state: dict) -> None:
        if not self.state_path:
            self._mem_state = dict(state)
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, self.state_path)

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock()).strftime("%Y-%m-%d")

    def usage(self) -> dict:
        """Today's turns, the cap and any cool-down — for a host's status line."""
        with _STATE_LOCK:
            state = self._read_state()
        used = state.get("used", 0) if state.get("day") == self._today() else 0
        until = float(state.get("cooldown_until") or 0)
        return {"day": self._today(), "used": used, "cap": self.daily_cap,
                "cooldown_until": until if until > self._clock() else None}

    def _take_turn(self) -> None:
        with _STATE_LOCK:
            state = self._read_state()
            if float(state.get("cooldown_until") or 0) > self._clock():
                raise CodexUnavailable("cooling_down", "Codex reported a usage or rate limit")
            if state.get("day") != self._today():
                state = {"day": self._today(), "used": 0}
            if self.daily_cap and state["used"] >= self.daily_cap:
                raise CodexUnavailable("daily_cap", f"{self.daily_cap} turns used today")
            state["used"] += 1
            self._write_state(state)

    def _cool_down(self) -> None:
        with _STATE_LOCK:
            state = self._read_state()
            state["cooldown_until"] = self._clock() + self.cooldown
            self._write_state(state)

    def _wait_for_host(self) -> None:
        if self._busy is None:
            return
        deadline = self._clock() + self.busy_wait
        while self._busy():
            if self._clock() >= deadline:
                raise CodexUnavailable("host_busy", "the host kept Codex busy")
            self._sleep(2.0)

    # --- LLM ------------------------------------------------------------------------------------------
    def complete(self, prompt: str, **kwargs) -> str:
        # timeout / max_tokens / num_retries come from LiteLLM-shaped callers (the console's model test);
        # a Codex turn has no token cap to set, and retrying is the job queue's decision, not ours.
        system = kwargs.get("system")
        timeout = float(kwargs.get("timeout") or self.timeout)
        runner = self._resolve_runner()
        with _GATE:
            self._wait_for_host()
            self._take_turn()
            try:
                text = runner(prompt, system=system, timeout=timeout)
            except CodexUnavailable:
                raise
            except Exception as exc:
                if _RATE_LIMIT.search(str(exc)):
                    self._cool_down()
                    raise CodexUnavailable("rate_limited", type(exc).__name__) from exc
                raise
        if not isinstance(text, str) or not text.strip():
            raise CodexUnavailable("empty_reply")
        return text
