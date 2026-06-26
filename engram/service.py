"""MemoryService — the multi-tenant memory core shared by every connection surface (CLAUDE.md §6).

One process, many isolated namespaces (one `Memory` per user/api-key), persisted to disk with an LRU of
hot in-RAM users. The HTTP API (`engram/server/app.py`), the MCP server (`engram/mcp/`), and the
OpenAI-compatible proxy all sit on top of this exact object, so a fix here is a fix everywhere and the
three surfaces can never drift apart.

Deliberately FastAPI-free: importing this pulls in only the core library (+ whatever embedder/LLM you
select), so the MCP server and the import CLI can use it without the web stack installed.
"""
from __future__ import annotations

import os
import shutil
import threading
from collections import OrderedDict
from typing import Any, Optional

from .memory import Memory
from .util import fmt_date, fmt_datetime

DEFAULT_DATA_DIR = os.path.expanduser("~/.engram/data")

# Frame the assembled memory for the answerer used by /v1/recall (the console's 问答 view).
_ANSWER_SYSTEM = (
    "你是用户的记忆助手。只依据下面提供的【记忆】回答用户的问题,简洁、准确、口语化。"
    "带日期的事实里最新的优先(这是知识更新);如果记忆里确实没有相关信息,就直接说「记忆里暂时没有这条」。"
)


def _answer_from_memory(answerer, query: str, ctx: str) -> str:
    """Generate an answer from the assembled lean context — what an agent using this memory would say.
    Best-effort: a missing answerer or a transient model error never breaks recall (returns "")."""
    if answerer is None or not ctx.strip():
        return ""
    try:
        return answerer.complete(f"【记忆】\n{ctx}\n\n【问题】{query}", system=_ANSWER_SYSTEM).strip()
    except Exception:  # noqa: BLE001 -- never let answering break recall
        return ""


def _est_tokens(text: str) -> int:
    """A token estimate that's fair for Chinese: each CJK char ≈ 1 token + each non-CJK word ≈ 1 token.
    (`len(text.split())` undercounts Chinese badly since it isn't whitespace-separated.)"""
    import re

    cjk = len(re.findall(r"[一-鿿]", text))
    words = len(re.findall(r"[A-Za-z0-9]+", re.sub(r"[一-鿿]", " ", text)))
    return cjk + words


class MemoryService:
    """Per-namespace persistent memory with a shared embedder/LLM and an LRU of hot users.

    Every operation takes a `user` (the namespace / api-key identity) and returns plain JSON-able
    dicts — the same payloads the HTTP routes return, so the MCP tools and the REST endpoints share
    one implementation and one contract.
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        embedder_name: Optional[str] = None,
        llm_name: Optional[str] = None,
        max_hot_users: Optional[int] = None,
    ) -> None:
        from .llm.providers import load_dotenv, make_embedder, make_llm

        load_dotenv()  # pick up provider keys (ARK_API_KEY, DEEPSEEK_API_KEY, ...) from a local .env
        self.data_dir = data_dir or os.environ.get("ENGRAM_DATA_DIR", DEFAULT_DATA_DIR)
        os.makedirs(self.data_dir, exist_ok=True)
        self.max_hot_users = max_hot_users or int(os.environ.get("ENGRAM_MAX_HOT_USERS", "64"))
        self.embedder = make_embedder(embedder_name or os.environ.get("ENGRAM_EMBEDDER", "bge-small"))
        name = llm_name if llm_name is not None else os.environ.get("ENGRAM_LLM", "")
        self.llm = make_llm(name) if name else None  # no LLM -> deterministic rule extractor
        # answerer for /v1/recall (the console's 问答) — a stronger model than the extractor when set;
        # otherwise reuse the main LLM. Lets the Ask page show a real answer, not just the context.
        answerer_name = os.environ.get("ENGRAM_ANSWERER", "")
        self.answerer = make_llm(answerer_name) if answerer_name else self.llm
        from .config import Config

        self.config = Config()
        # opt-in System-2 LLM conflict detection -> the detect->confirm loop (needs an LLM). Off by
        # default so the offline/zero-setup path stays deterministic (pure-rules conflict handling).
        if os.environ.get("ENGRAM_CONFLICT_DETECTION") == "1" and self.llm is not None:
            self.config.conflict_detection = True
        self._hot: "OrderedDict[str, Memory]" = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._g = threading.Lock()

    # --- namespace lifecycle ------------------------------------------------
    def _path(self, user: str) -> str:
        safe = "".join(c for c in user if c.isalnum() or c in "-_.") or "default"
        path = os.path.join(self.data_dir, safe)
        legacy = os.path.join(self.data_dir, f"{safe}.pkl")
        return legacy if os.path.exists(legacy) and not os.path.exists(path) else path

    def lock(self, user: str) -> threading.Lock:
        with self._g:
            return self._locks.setdefault(user, threading.Lock())

    def get(self, user: str) -> Memory:
        """The hot Memory for `user`, loading its disk snapshot on first touch and evicting the coldest
        namespace from RAM when over capacity (its snapshot stays on disk)."""
        with self._g:
            if user in self._hot:
                self._hot.move_to_end(user)
                return self._hot[user]
        mem = Memory.open(self._path(user), embedder=self.embedder, llm=self.llm, config=self.config)
        with self._g:
            self._hot[user] = mem
            self._hot.move_to_end(user)
            while len(self._hot) > self.max_hot_users:
                self._hot.popitem(last=False)
        return mem

    def forget(self, user: str) -> dict:
        with self._g:
            self._hot.pop(user, None)
        p = self._path(user)
        safe = "".join(c for c in user if c.isalnum() or c in "-_.") or "default"
        for p in {p, os.path.join(self.data_dir, safe), os.path.join(self.data_dir, f"{safe}.pkl")}:
            if os.path.exists(p):
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
        return {"ok": True, "message": f"all memory for '{user}' erased"}

    @property
    def hot_count(self) -> int:
        return len(self._hot)

    # --- write path ---------------------------------------------------------
    def remember(self, user: str, content: str, session_id: str = "default",
                 scope: str = "auto") -> dict:
        """Store a message + run System-2 consolidation/summarization (best-effort: a transient model
        outage never loses the raw episode). `scope` (auto|long|working) routes by ephemerality —
        transient state stays in dated history + working memory but is NOT promoted to a durable profile
        fact (see Memory.remember)."""
        with self.lock(user):
            mem = self.get(user)
            routed = mem.remember(content, user_id=user, session_id=session_id, scope=scope)
            if routed["scope"] == "working":
                mem.save()
                return {"ok": True, "scope": "working", "kind": routed["kind"],
                        "id": routed["working_id"], "episode_id": routed["episode_id"],
                        "note": "kept in dated history (askable later); not added to the durable profile"}
            try:
                added = mem.consolidate().get("facts_added", 0)
                mem.summarize_episodes(list(mem.episodes_doc.values()))
            except Exception as exc:  # noqa: BLE001 — keep the raw episode no matter what
                mem.save()
                return {"ok": True, "extracted": 0, "degraded": type(exc).__name__, "stored_raw": True}
            mem.save()
            return {"ok": True, "scope": "long", "extracted": added,
                    "total_facts": len([f for f in mem.fact_store.values() if f.is_live()])}

    def import_(self, user: str, sessions: Optional[list] = None, format: str = "auto",
                data: Any = None, consolidate: bool = True, summarize: bool = True,
                session_id: str = "imported") -> dict:
        """Bulk import: either pre-parsed `sessions` (list of ImportSession/dicts) OR raw `data` to parse
        with `format` (chatgpt/messages/records/jsonl/transcript/auto). One batched ingest + consolidation."""
        with self.lock(user):
            mem = self.get(user)
            if sessions is None:
                from .connectors import parse
                sessions = parse(data, format=format, session_id=session_id)
            stats = mem.import_messages(sessions, user_id=user, consolidate=consolidate,
                                        summarize=summarize)
            mem.save()
            return {"ok": True, **stats}

    def add_fact(self, user: str, subject: str, predicate: str, object: str) -> dict:
        with self.lock(user):
            mem = self.get(user)
            f = mem.add_fact(subject, predicate, object, user_id=user)
            mem.save()
            return {"ok": True, "id": f.id, "text": f.text}

    def update_fact(self, user: str, fact_id: str, subject: Optional[str] = None,
                    predicate: Optional[str] = None, object: Optional[str] = None,
                    sensitive: Optional[bool] = None, category: Optional[str] = None) -> Optional[dict]:
        with self.lock(user):
            mem = self.get(user)
            f = mem.update_fact(fact_id, subject=subject, predicate=predicate, object=object,
                                sensitive=sensitive, category=category)
            if f is None:
                return None
            mem.save()
            return {"ok": True, "id": f.id, "text": f.text}

    def delete_fact(self, user: str, fact_id: str) -> dict:
        with self.lock(user):
            mem = self.get(user)
            ok = mem.delete_fact(fact_id)
            mem.save()
            return {"ok": ok}

    def set_focus(self, user: str, track: Optional[list[str]] = None,
                  mute: Optional[list[str]] = None) -> dict:
        with self.lock(user):
            mem = self.get(user)
            focus = mem.set_focus(track=track, mute=mute)
            mem.save()
            return {"ok": True, "focus": focus}

    def set_policy(self, user: str, **fields: Optional[str]) -> dict:
        with self.lock(user):
            mem = self.get(user)
            clean = {k: v for k, v in fields.items() if v is not None}
            result = mem.set_policy(**clean)
            mem.save()
            return {"ok": True, **result}

    # --- read path ----------------------------------------------------------
    def recall(self, user: str, query: str, lean: bool = True, n_chunks: int = 6,
               session_id: Optional[str] = None, answer: bool = False) -> dict:
        """A small retrieved context (lean) or a direct factual answer (lean=False). When `session_id` is
        set, the lean context also surfaces that session's ephemeral working memory.

        `answer=True` (the HTTP /v1/recall path, for the console's 问答 view) additionally generates a
        real answer over that context AND reports the full-context baseline token count, so the UI can
        show the token saving. It costs one extra LLM call, so the MCP/OpenAI-compat surfaces — which
        only need the context to inject — leave it off (the default)."""
        mem = self.get(user)
        if lean:
            ctx = mem.lean_context(query, user_id=user, n_chunks=n_chunks, session_id=session_id)
            out = {"context": ctx, "tokens_est": _est_tokens(ctx)}
            if answer:
                # full-context baseline: what it would cost to stuff the ENTIRE history into the prompt
                full = "\n".join(ep.content for ep in mem.episodes_doc.values())
                out["full_tokens"] = _est_tokens(full)
                out["answer"] = _answer_from_memory(self.answerer, query, ctx)
            return out
        res = mem.search(query, user_id=user)
        return {"answer": res.answer(), "facts": [f.text for f in res.facts[:10]]}

    def structured_profile(self, user: str) -> dict:
        """L2 structured profile (basic info / preferences / habits, confirmed vs tentative). Display-only."""
        return self.get(user).structured_profile(user)

    # --- suspected conflicts (LLM-detected in System-2, user-confirmed; never auto-resolved) --------
    def conflicts(self, user: str) -> dict:
        """Suspected conflicts awaiting the user's decision. Texts are localized for display."""
        from .localize import display_of

        mem = self.get(user)

        def disp(fid: str, fallback: str) -> str:
            f = mem.fact_store.get(fid)
            return display_of(f) if f is not None else fallback

        return {"conflicts": [{
            "id": c.id, "older": c.older, "newer": c.newer,
            "older_text": disp(c.older, c.text_older), "newer_text": disp(c.newer, c.text_newer),
            "reason": c.reason,
        } for c in mem.pending_conflicts(user)]}

    def resolve_conflict(self, user: str, conflict_id: str, keep: str = "newer") -> dict:
        """Apply the user's decision: keep newer/older (supersede the other) or both (dismiss). The user
        is authoritative — we never silently overwrite; this is the human-confirmed end of the loop."""
        with self.lock(user):
            mem = self.get(user)
            ok = (mem.dismiss_conflict(conflict_id) if keep == "both"
                  else mem.resolve_conflict(conflict_id, keep=keep))
            mem.save()
            return {"ok": ok}

    # --- working memory (ephemeral, session/TTL-scoped; feature ①) ----------
    def add_working(self, user: str, content: str, session_id: str = "default", kind: str = "state",
                    ttl_seconds: Optional[float] = None) -> dict:
        with self.lock(user):
            mem = self.get(user)
            wm = mem.remember_working(content, user_id=user, session_id=session_id, kind=kind,
                                      ttl_seconds=ttl_seconds)
            mem.save()
            return {"ok": True, "id": wm.id, "kind": wm.kind, "expires_at": wm.expires_at}

    def working_memory(self, user: str, session_id: Optional[str] = None) -> dict:
        mem = self.get(user)
        items = mem.working_memory(user, session_id=session_id)
        return {"items": [{
            "id": w.id, "content": w.content, "kind": w.kind, "session_id": w.session_id,
            "created": fmt_date(w.created_at),
            "expires_at": fmt_date(w.expires_at) if w.expires_at else None,
        } for w in items]}

    def clear_working(self, user: str, session_id: str) -> dict:
        with self.lock(user):
            mem = self.get(user)
            n = mem.clear_session(user, session_id)
            mem.save()
            return {"ok": True, "cleared": n}

    def profile(self, user: str) -> dict:
        mem = self.get(user)
        return {"profile": mem.build_persona(user),
                "facts": [f.text for f in mem.fact_store.values() if f.is_live()][:50]}

    def get_focus(self, user: str) -> dict:
        return self.get(user).get_focus()

    def get_policy(self, user: str) -> dict:
        return self.get(user).get_policy()

    def graph(self, user: str) -> dict:
        return self.get(user).graph_data(user)

    def memories(self, user: str) -> dict:
        """Everything stored for this user: profile, counts, bi-temporal facts (live + superseded with
        provenance), raw episodes + L2 summaries. The 'look inside my memory' payload."""
        from .localize import display_of  # localized rendering for Chinese-recorded facts

        mem = self.get(user)
        facts = sorted(mem.fact_store.values(), key=lambda f: f.valid_at, reverse=True)
        return {
            "user": user,
            "profile": mem.build_persona(user),
            "counts": {"episodes": len(mem.episodes_doc.values()),
                       "facts_live": sum(1 for f in mem.fact_store.values() if f.is_live()),
                       "facts_superseded": sum(1 for f in mem.fact_store.values() if not f.is_live()),
                       "summaries": len(mem.summary_vec.values())},
            "facts": [{
                "id": f.id, "text": f.text, "display": display_of(f),
                "subject": f.subject, "predicate": f.predicate,
                "object": f.object, "valid_at": fmt_datetime(f.valid_at),
                "invalid_at": fmt_datetime(f.invalid_at) if f.invalid_at else None,
                "status": "live" if f.is_live() else "superseded",
                "source": f.source, "supersedes": f.supersedes,
                "category": getattr(f, "category", ""), "sensitive": getattr(f, "sensitive", False),
                "salience": round(f.salience, 2), "provenance": f.provenance,
            } for f in facts],
            "episodes": [{"date": ep.metadata.get("date") or fmt_date(ep.event_time),
                          "session": ep.session_id, "content": ep.content[:500],
                          "summary": ep.summary} for ep in mem.episodes_doc.values()],
        }

    def export(self, user: str, include_sensitive: bool = True) -> dict:
        """Full-fidelity data export (GDPR-style portability): every fact's bi-temporal stamps +
        provenance, raw episodes, summaries, profile, focus, and graph. `include_sensitive=False` redacts
        facts tagged sensitive (feature ⑤) for safe sharing."""
        mem = self.get(user)
        _facts = [f for f in mem.fact_store.values()
                  if include_sensitive or not getattr(f, "sensitive", False)]
        return {
            "engram_export_version": 1,
            "user": user,
            "profile": mem.build_persona(user),
            "focus": mem.get_focus(),
            "facts": [{
                "id": f.id, "subject": f.subject, "predicate": f.predicate, "object": f.object,
                "text": f.text, "source": f.source, "status": "live" if f.is_live() else "superseded",
                "category": getattr(f, "category", ""), "sensitive": getattr(f, "sensitive", False),
                "salience": round(f.salience, 3), "confidence": f.confidence,
                "valid_at": f.valid_at, "valid_at_h": fmt_date(f.valid_at),
                "invalid_at": f.invalid_at, "invalid_at_h": fmt_date(f.invalid_at) if f.invalid_at else None,
                "created_at": f.created_at, "expired_at": f.expired_at,
                "supersedes": f.supersedes, "provenance": f.provenance,
            } for f in sorted(_facts, key=lambda x: x.valid_at, reverse=True)],
            "episodes": [{
                "id": ep.id, "session_id": ep.session_id, "speaker": ep.speaker,
                "event_time": ep.event_time, "date": ep.metadata.get("date") or fmt_date(ep.event_time),
                "content": ep.content, "summary": ep.summary,
            } for ep in mem.episodes_doc.values()],
            "graph": mem.graph_data(user),
        }
