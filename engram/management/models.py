"""Administrator-owned runtime models. Credentials never enter a read response."""
from __future__ import annotations

import copy
import threading
import time
from urllib.parse import urlsplit

ROLES = ("extractor", "answerer", "vision")


class ModelManager:
    def __init__(self, service) -> None:
        self.service = service
        self.store = service.management_store
        self._lock = threading.RLock()
        self.defaults = {"extractor": service.llm, "answerer": service.answerer,
                         "vision": service.captioner}
        self.startup_error = None
        try:
            self._apply()
        except Exception as exc:
            # A removed optional backend must not make stored memory unreadable.
            self.startup_error = type(exc).__name__

    def _record(self, role: str) -> dict:
        if role not in ROLES:
            raise ValueError("unknown model role")
        return self.store.get("", "models", role) or {"role": role, "mode": "inherit"}

    @staticmethod
    def _client(record: dict):
        from ..llm.litellm_llm import LiteLLMClient

        model = record["model"]
        model = {"deepseek": "deepseek/deepseek-chat", "deepseek-chat": "deepseek/deepseek-chat",
                 "deepseek-reasoner": "deepseek/deepseek-reasoner"}.get(model, model)
        kwargs = {"timeout": record.get("timeout", 30), "num_retries": 0}
        if record.get("api_base"):
            kwargs["api_base"] = record["api_base"]
        if record.get("api_key"):
            kwargs["api_key"] = record["api_key"]
        return LiteLLMClient(model, max_attempts=1, **kwargs)

    def _resolved(self) -> dict:
        clients = {}
        for role in ROLES:
            record = self._record(role)
            if record["mode"] == "offline":
                clients[role] = None
            elif record["mode"] == "custom":
                clients[role] = self._client(record)
            else:
                clients[role] = self.defaults[role]
                if role != "extractor" and self.defaults[role] is self.defaults["extractor"]:
                    clients[role] = clients["extractor"]
        return clients

    def _apply(self) -> None:
        clients = self._resolved()
        svc = self.service
        svc.llm, svc.answerer, svc.captioner = (clients[r] for r in ROLES)
        # Runtime objects are rebuilt around the same data stores; no vectors or facts are migrated.
        with svc._g:
            users = list(svc._hot)
        for user in users:
            with svc.write_lock(user):
                mem = svc.get(user)
                mem.llm = svc.llm
                mem.summarizer.llm = svc.llm
                mem._rewire()
                mem._apply_policy()
                mem._persona_cache.clear()
        self.startup_error = None

    def view(self) -> dict:
        rows = []
        for role in ROLES:
            record = self._record(role)
            runtime = {"extractor": self.service.llm, "answerer": self.service.answerer,
                       "vision": self.service.captioner}[role]
            rows.append({"role": role, "mode": record["mode"],
                         "model": record.get("model", getattr(runtime, "model", "")),
                         "api_base": record.get("api_base", ""),
                         "key_present": bool(record.get("api_key")),
                         "timeout": record.get("timeout", 30),
                         "configured": runtime is not None,
                         "updated_at": record.get("updated_at"),
                         "last_test": self.store.get("", "model_tests", role)})
        return {"roles": rows, "startup_error": self.startup_error}

    def configure(self, role: str, payload: dict) -> dict:
        if role not in ROLES:
            raise ValueError("unknown model role")
        mode = payload.get("mode", "inherit")
        if mode not in {"inherit", "custom", "offline"}:
            raise ValueError("unknown model mode")
        with self._lock:
            previous = self._record(role)
            record = {"role": role, "mode": mode, "updated_at": time.time()}
            if mode == "custom":
                model = str(payload.get("model", "")).strip()
                if not model or len(model) > 200 or any(c in model for c in "\r\n"):
                    raise ValueError("provide a valid model identifier")
                base = str(payload.get("api_base", "")).strip().rstrip("/")
                if base:
                    url = urlsplit(base)
                    if (url.scheme not in {"http", "https"} or not url.hostname or url.username
                            or url.password or url.query or url.fragment or len(base) > 500):
                        raise ValueError("model endpoint must be an HTTP(S) URL without credentials or query")
                timeout = payload.get("timeout", 30)
                if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
                    raise ValueError("model timeout must be between 1 and 120 seconds")
                key = payload.get("api_key")
                if key is None:
                    # A changed destination must not silently receive the previous provider's secret.
                    key = previous.get("api_key", "") if base == previous.get("api_base", "") else ""
                if payload.get("clear_key"):
                    key = ""
                if not isinstance(key, str) or len(key) > 4096 or any(c in key for c in "\r\n"):
                    raise ValueError("invalid credential")
                record.update(model=model, api_base=base, api_key=key, timeout=float(timeout))
                self._client(record)  # Validate the optional runtime before storing anything.
            self.store.put("", "models", role, record)
            try:
                self._apply()
            except Exception:
                self.store.put("", "models", role, previous)
                self._apply()
                raise
            # A check of the previous configuration must not mark this one as connected.
            self.store.delete("", "model_tests", role)
            return self.view()

    def test(self, role: str) -> dict:
        if role not in ROLES:
            raise ValueError("unknown model role")
        with self._lock:
            record = self._record(role)
            client = {"extractor": self.service.llm, "answerer": self.service.answerer,
                      "vision": self.service.captioner}[role]
            started = time.monotonic()
            result = {"role": role, "tested_at": time.time(), "ok": False,
                      "scope": "text_completion", "error": None}
            if client is None:
                result["error"] = "model_not_configured"
            else:
                try:
                    # Do not modify the live client's retry policy while concurrent jobs use it.
                    probe = copy.copy(client)
                    if hasattr(probe, "max_attempts"):
                        probe.max_attempts = 1
                    response = probe.complete("Reply with the single word OK.",
                                              timeout=min(record.get("timeout", 30), 15),
                                              max_tokens=8, num_retries=0)
                    result["ok"] = bool(str(response).strip())
                    if not result["ok"]:
                        result["error"] = "empty_response"
                except Exception as exc:
                    # Exception strings can echo provider keys, URLs and user text.
                    result["error"] = type(exc).__name__
            result["latency_ms"] = round((time.monotonic() - started) * 1000)
            self.store.put("", "model_tests", role, result)
            return result
