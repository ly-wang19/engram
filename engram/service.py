"""MemoryService — the multi-tenant memory core shared by every connection surface (CLAUDE.md §6).

One process, many isolated namespaces (one `Memory` per user/api-key), persisted to disk with an LRU of
hot in-RAM users. The HTTP API (`engram/server/app.py`), the MCP server (`engram/mcp/`), and the
OpenAI-compatible proxy all sit on top of this exact object, so a fix here is a fix everywhere and the
three surfaces can never drift apart.

Deliberately FastAPI-free: importing this pulls in only the core library (+ whatever embedder/LLM you
select), so the MCP server and the import CLI can use it without the web stack installed.
"""
from __future__ import annotations

import hashlib
import os
import queue
import re
import shutil
import threading
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Optional

from .memory import Memory
from .metrics import Metrics, timed
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


def _all_facts(mem: Memory) -> list:
    return mem.fact_store.values() + mem.cold_store.values()


def _clamp_limit(limit: Optional[int], default: Optional[int] = None, max_value: int = 500) -> Optional[int]:
    if limit is None:
        return default
    return max(0, min(int(limit), max_value))


def _page(items: list, offset: int = 0, limit: Optional[int] = None) -> tuple[list, dict]:
    start = max(0, int(offset or 0))
    effective_limit = None
    if limit is None:
        sliced = items[start:]
        next_offset = None
    else:
        effective_limit = _clamp_limit(limit, max_value=500)
        if effective_limit == 0:
            sliced = []
            next_offset = None
        else:
            sliced = items[start:start + effective_limit]
            end = start + effective_limit
            next_offset = end if end < len(items) else None
    return sliced, {
        "total": len(items),
        "offset": start,
        "limit": effective_limit,
        "has_more": next_offset is not None,
        "next_offset": next_offset,
    }


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
        self.data_dir = os.path.abspath(
            os.path.expanduser(data_dir or os.environ.get("ENGRAM_DATA_DIR", DEFAULT_DATA_DIR))
        )
        os.makedirs(self.data_dir, exist_ok=True)
        self.max_hot_users = max_hot_users or int(os.environ.get("ENGRAM_MAX_HOT_USERS", "64"))
        self.embedder = make_embedder(embedder_name or os.environ.get("ENGRAM_EMBEDDER", "hashing"))
        name = llm_name if llm_name is not None else os.environ.get("ENGRAM_LLM", "")
        self.llm = make_llm(name) if name else None  # no LLM -> deterministic rule extractor
        # answerer for /v1/recall (the console's 问答) — a stronger model than the extractor when set;
        # otherwise reuse the main LLM. Lets the Ask page show a real answer, not just the context.
        answerer_name = os.environ.get("ENGRAM_ANSWERER", "")
        self.answerer = make_llm(answerer_name) if answerer_name else self.llm
        # Vision captioner for multimodal ingest (CLAUDE.md §6): turns an image into searchable caption
        # text. Defaults to the main LLM (many chat models are vision-capable); set ENGRAM_VISION_LLM for a
        # dedicated vision model. None => images stored as a placeholder, so the offline path stays text-only.
        vision_name = os.environ.get("ENGRAM_VISION_LLM", "")
        self.captioner = make_llm(vision_name) if vision_name else self.llm
        from .config import Config

        self.config = Config()
        if os.environ.get("ENGRAM_MAX_HOT_FACTS"):
            self.config.max_hot_facts = int(os.environ["ENGRAM_MAX_HOT_FACTS"])
        # opt-in System-2 LLM conflict detection -> the detect->confirm loop (needs an LLM). Off by
        # default so the offline/zero-setup path stays deterministic (pure-rules conflict handling).
        if os.environ.get("ENGRAM_CONFLICT_DETECTION") == "1" and self.llm is not None:
            self.config.conflict_detection = True
        self._hot: "OrderedDict[str, Memory]" = OrderedDict()
        self._hot_versions: dict[str, tuple[int, int] | None] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._g = threading.Lock()
        # Live service metrics (Bet D online): latency percentiles on the SLO-bearing paths + the token-
        # savings ratio, exposed aggregate-only at GET /metrics. See engram/metrics.py.
        self.metrics = Metrics()
        # System-1 / System-2 split (CLAUDE.md Bet F). When enabled, remember() does only the fast System-1
        # write (append + light embed + a durable save) and hands consolidation to a background worker, so
        # the write path stays low-latency. OFF by default — the synchronous path keeps the
        # remember-then-immediately-queryable semantics the console, tests, and current callers rely on.
        self._async = os.environ.get("ENGRAM_ASYNC_CONSOLIDATION") == "1"
        self._queue: "Optional[queue.Queue]" = None
        self._pending: set[str] = set()  # users with consolidation queued (coalesce repeat enqueues)
        self._worker: Optional[threading.Thread] = None
        if self._async:
            self._queue = queue.Queue()
            self._worker = threading.Thread(target=self._consume, name="engram-system2", daemon=True)
            self._worker.start()

    # --- namespace lifecycle ------------------------------------------------
    def _safe_user(self, user: str) -> str:
        """Return a deterministic, collision-resistant directory name for a logical namespace.

        The readable prefix is diagnostic only. Identity comes from the digest of the untouched UTF-8
        namespace, so punctuation removal and Unicode normalization can never merge two tenants.
        """
        raw = str(user)
        prefix = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").lower()[:48]
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"{prefix or 'namespace'}--{digest}"

    def _contained_child(self, name: str) -> str:
        root = os.path.realpath(self.data_dir)
        path = os.path.realpath(os.path.join(root, name))
        if os.path.dirname(path) != root or path == root:
            raise ValueError("namespace path must be a direct child of ENGRAM_DATA_DIR")
        return path

    def _legacy_safe_user(self, user: str) -> Optional[str]:
        """Return an old directory name only when the old sanitizer changed nothing at all."""
        raw = str(user)
        if not raw or raw in {".", ".."} or os.path.isabs(raw):
            return None
        if os.path.basename(raw) != raw:
            return None
        if not all(c.isalnum() or c in "-_." for c in raw):
            return None
        return raw

    def _secure_path(self, user: str) -> str:
        return self._contained_child(self._safe_user(user))

    def _legacy_paths(self, user: str) -> tuple[str, ...]:
        safe = self._legacy_safe_user(user)
        if safe is None:
            return ()
        return (
            self._contained_child(safe),
            self._contained_child(f"{safe}.pkl"),
        )

    def _path(self, user: str) -> str:
        secure = self._secure_path(user)
        if os.path.exists(secure):
            return secure
        legacy_paths = self._legacy_paths(user)
        if legacy_paths:
            legacy_dir, legacy_pickle = legacy_paths
            if os.path.exists(legacy_dir):
                return legacy_dir
            if os.path.exists(legacy_pickle):
                return legacy_pickle
        return secure

    @contextmanager
    def _user_file_lock(self, user: str):
        path = self._path(user)
        if path.endswith(".pkl") or (os.path.exists(path) and not os.path.isdir(path)):
            os.makedirs(self.data_dir, exist_ok=True)
            lock_path = self._contained_child(f"{self._safe_user(user)}.service.lock")
        else:
            os.makedirs(path, exist_ok=True)
            if os.path.dirname(os.path.realpath(path)) != os.path.realpath(self.data_dir):
                raise ValueError("namespace directory escaped ENGRAM_DATA_DIR")
            lock_path = os.path.join(path, ".service.lock")
        with open(lock_path, "a", encoding="utf-8") as fh:
            try:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                yield
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except ImportError:  # pragma: no cover - fcntl exists on supported Linux/macOS targets.
                yield

    @contextmanager
    def write_lock(self, user: str):
        """Serialize local read-modify-save transactions across threads and MCP stdio processes."""
        with self.lock(user):
            with self._user_file_lock(user):
                yield

    def _store_version(self, user: str) -> tuple[int, int] | None:
        manifest = os.path.join(self._path(user), "manifest.json")
        try:
            st = os.stat(manifest)
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _save(self, user: str, mem: Memory) -> None:
        mem.save()
        with self._g:
            if self._hot.get(user) is mem:
                self._hot_versions[user] = self._store_version(user)

    def lock(self, user: str) -> threading.Lock:
        with self._g:
            return self._locks.setdefault(user, threading.Lock())

    def get(self, user: str) -> Memory:
        """The hot Memory for `user`, reloading when another local process saved a newer snapshot.

        Agent clients often run as separate MCP stdio processes (Codex, Claude Code, Cursor) that share
        one user-owned data dir. Checking the manifest fingerprint on every access keeps those processes
        from serving stale in-RAM state after a sibling agent writes to disk.
        """
        with self._g:
            if user in self._hot:
                current = self._store_version(user)
                if current != self._hot_versions.get(user):
                    self._hot.pop(user, None)
                    self._hot_versions.pop(user, None)
                else:
                    self._hot.move_to_end(user)
                    return self._hot[user]
        mem = Memory.open(self._path(user), embedder=self.embedder, llm=self.llm, config=self.config)
        version = self._store_version(user)
        with self._g:
            self._hot[user] = mem
            self._hot_versions[user] = version
            self._hot.move_to_end(user)
            while len(self._hot) > self.max_hot_users:
                evicted, _ = self._hot.popitem(last=False)
                self._hot_versions.pop(evicted, None)
        return mem

    def forget(self, user: str) -> dict:
        with self.write_lock(user):
            with self._g:
                self._hot.pop(user, None)
                self._hot_versions.pop(user, None)
            targets = {self._secure_path(user), *self._legacy_paths(user)}
            for target in targets:
                # Re-check immediately before deletion so symlink swaps cannot widen the target.
                checked = self._contained_child(os.path.basename(target))
                if os.path.exists(checked):
                    if os.path.isdir(checked):
                        shutil.rmtree(checked)
                    else:
                        os.remove(checked)
        return {"ok": True, "message": f"all memory for '{user}' erased"}

    @property
    def hot_count(self) -> int:
        return len(self._hot)

    def metrics_snapshot(self) -> dict:
        """The /metrics payload: latency percentiles + counters + token savings (from Metrics), plus the
        service-level gauges (hot namespaces in RAM, async System-2 backlog). Aggregate numbers only."""
        snap = self.metrics.snapshot()
        snap["users_hot"] = self.hot_count
        snap["async"] = {
            "enabled": self._async,
            "queue_depth": self._queue.qsize() if self._queue is not None else 0,
            "pending_users": len(self._pending),
        }
        return snap

    # --- async System-2 consolidation (CLAUDE.md Bet F; opt-in via ENGRAM_ASYNC_CONSOLIDATION=1) --------
    def _enqueue(self, user: str) -> None:
        """Schedule a user's pending episodes for background consolidation, coalescing repeat requests."""
        with self._g:
            if user in self._pending:
                return
            self._pending.add(user)
        self._queue.put(user)  # type: ignore[union-attr]

    def _consume(self) -> None:
        """Background worker: drain the queue and run System-2 (consolidate + summarize) off the write path."""
        while True:
            user = self._queue.get()  # type: ignore[union-attr]
            if user is None:  # shutdown sentinel
                self._queue.task_done()  # type: ignore[union-attr]
                break
            # Clear pending BEFORE consolidating, so an episode that arrives mid-pass re-enqueues a follow-up
            # pass instead of being silently skipped (consolidate() only drains not-yet-consolidated episodes,
            # so a redundant pass is cheap and idempotent).
            with self._g:
                self._pending.discard(user)
            try:
                self._consolidate_now(user)
            except Exception:  # noqa: BLE001 — a bad job must never kill the worker
                pass
            finally:
                self._queue.task_done()  # type: ignore[union-attr]

    def _consolidate_now(self, user: str) -> None:
        with self.write_lock(user):
            mem = self.get(user)
            try:
                mem.consolidate()
                mem.summarize_episodes(list(mem.episodes_doc.values()))
            finally:
                self._save(user, mem)

    def flush(self) -> None:
        """Block until all queued System-2 consolidation has completed (for tests / graceful shutdown).
        No-op in synchronous mode."""
        if self._queue is not None:
            self._queue.join()

    def close(self) -> None:
        """Stop the background worker (best-effort). Safe to call when async is off."""
        if self._worker is not None and self._queue is not None:
            self._queue.put(None)  # sentinel
            self._worker.join(timeout=5)
            self._worker = None

    # --- write path ---------------------------------------------------------
    @timed("remember")
    def remember(self, user: str, content: str, session_id: str = "default",
                 scope: str = "auto") -> dict:
        """Store a message + run System-2 consolidation/summarization (best-effort: a transient model
        outage never loses the raw episode). `scope` (auto|long|working) routes by ephemerality —
        transient state stays in dated history + working memory but is NOT promoted to a durable profile
        fact (see Memory.remember)."""
        with self.write_lock(user):
            mem = self.get(user)
            routed = mem.remember(content, user_id=user, session_id=session_id, scope=scope)
            if routed["scope"] == "working":
                self._save(user, mem)
                return {"ok": True, "scope": "working", "kind": routed["kind"],
                        "id": routed["working_id"], "episode_id": routed["episode_id"],
                        "note": "kept in dated history (askable later); not added to the durable profile"}
            if self._async:
                # System-1 only: the episode is embedded + durably saved here; System-2 (consolidate +
                # summarize) runs in the background worker so the write returns immediately (Bet F).
                self._save(user, mem)
                self._enqueue(user)
                return {"ok": True, "scope": "long", "queued": True, "episode_id": routed.get("episode_id"),
                        "note": "consolidation scheduled off the write path (async System-2)"}
            try:
                added = mem.consolidate().get("facts_added", 0)
                mem.summarize_episodes(list(mem.episodes_doc.values()))
            except Exception as exc:  # noqa: BLE001 — keep the raw episode no matter what
                self._save(user, mem)
                self.metrics.count("remember_degraded")
                return {"ok": True, "extracted": 0, "degraded": type(exc).__name__, "stored_raw": True}
            self._save(user, mem)
            return {"ok": True, "scope": "long", "extracted": added,
                    "total_facts": len([f for f in _all_facts(mem) if f.is_live()])}

    @timed("import")
    def import_(self, user: str, sessions: Optional[list] = None, format: str = "auto",
                data: Any = None, consolidate: bool = True, summarize: bool = True,
                session_id: str = "imported", dedupe: bool = True) -> dict:
        """Bulk import: either pre-parsed `sessions` (list of ImportSession/dicts) OR raw `data` to parse
        with `format` (chatgpt/messages/records/jsonl/transcript/auto). One batched ingest + consolidation.
        Idempotent by default: re-posting the same export skips already-ingested episodes (`skipped`)."""
        with self.write_lock(user):
            mem = self.get(user)
            if sessions is None:
                from .connectors import parse
                sessions = parse(data, format=format, session_id=session_id)
            stats = mem.import_messages(sessions, user_id=user, consolidate=consolidate,
                                        summarize=summarize, dedupe=dedupe)
            self._save(user, mem)
            return {"ok": True, **stats}

    @timed("import_document")
    def import_document(self, user: str, data, filename: Optional[str] = None,
                        content_type: Optional[str] = None, session_id: str = "document",
                        consolidate: bool = True, summarize: bool = True) -> dict:
        """Ingest one uploaded document or image (CLAUDE.md §6 multimodal). PDF/DOCX/text are flattened to
        text; an image is captioned by the vision model (or stored as a placeholder when none is configured)
        — either way it enters the same System-2 pipeline as everything else, so it's immediately searchable."""
        from .connectors.documents import detect_kind, document_text, to_data_url, to_session
        from .llm import vision

        kind = detect_kind(data, filename, content_type)
        if kind == "image":
            cap = vision.caption_image(self.captioner, to_data_url(data, content_type))
            text = f"[image] {cap}" if cap else "[image] (no caption — set ENGRAM_VISION_LLM to describe images)"
            session = to_session(text, filename=filename, session_id=session_id,
                                 metadata={"kind": "image", "media_type": content_type or ""})
        else:
            try:
                text = document_text(data, kind)
            except ImportError as exc:  # optional extractor dep missing -> actionable error, not a 500
                return {"ok": False, "kind": kind, "error": str(exc)}
            if not text:
                return {"ok": False, "kind": kind, "error": f"no extractable text in {filename or kind}"}
            session = to_session(text, filename=filename, session_id=session_id,
                                 metadata={"kind": kind, "media_type": content_type or ""})
        with self.write_lock(user):
            mem = self.get(user)
            stats = mem.import_messages([session], user_id=user, consolidate=consolidate,
                                        summarize=summarize)
            self._save(user, mem)
        return {"ok": True, "kind": kind, **stats}

    def add_fact(
        self,
        user: str,
        subject: str,
        predicate: str,
        object: str,
        sensitive: Optional[bool] = None,
        category: Optional[str] = None,
    ) -> dict:
        with self.write_lock(user):
            mem = self.get(user)
            f = mem.add_fact(
                subject,
                predicate,
                object,
                user_id=user,
                sensitive=sensitive,
                category=category,
            )
            self._save(user, mem)
            return {"ok": True, "id": f.id, "text": f.text}

    def update_fact(self, user: str, fact_id: str, subject: Optional[str] = None,
                    predicate: Optional[str] = None, object: Optional[str] = None,
                    sensitive: Optional[bool] = None, category: Optional[str] = None) -> Optional[dict]:
        with self.write_lock(user):
            mem = self.get(user)
            f = mem.update_fact(fact_id, subject=subject, predicate=predicate, object=object,
                                sensitive=sensitive, category=category)
            if f is None:
                return None
            self._save(user, mem)
            return {"ok": True, "id": f.id, "text": f.text}

    def delete_fact(self, user: str, fact_id: str) -> dict:
        with self.write_lock(user):
            mem = self.get(user)
            ok = mem.delete_fact(fact_id)
            self._save(user, mem)
            return {"ok": ok}

    def set_focus(self, user: str, track: Optional[list[str]] = None,
                  mute: Optional[list[str]] = None) -> dict:
        with self.write_lock(user):
            mem = self.get(user)
            focus = mem.set_focus(track=track, mute=mute)
            self._save(user, mem)
            return {"ok": True, "focus": focus}

    def set_policy(self, user: str, **fields: Optional[str]) -> dict:
        with self.write_lock(user):
            mem = self.get(user)
            clean = {k: v for k, v in fields.items() if v is not None}
            result = mem.set_policy(**clean)
            self._save(user, mem)
            return {"ok": True, **result}

    # --- read path ----------------------------------------------------------
    @timed("recall")
    def recall(self, user: str, query: str, lean: bool = True, n_chunks: int = 6,
               session_id: Optional[str] = None, as_of: Optional[float] = None,
               redact_sensitive: bool = False,
               answer: bool = False) -> dict:
        """A small retrieved context (lean) or a direct factual answer (lean=False). When `session_id` is
        set, the lean context also surfaces that session's ephemeral working memory.

        `answer=True` (the HTTP /v1/recall path, for the console's 问答 view) additionally generates a
        real answer over that context AND reports the full-context baseline token count, so the UI can
        show the token saving. It costs one extra LLM call, so the MCP/OpenAI-compat surfaces — which
        only need the context to inject — leave it off (the default)."""
        mem = self.get(user)
        if lean:
            ctx = mem.lean_context(query, user_id=user, n_chunks=n_chunks, session_id=session_id,
                                   as_of=as_of, redact_sensitive=redact_sensitive)
            out = {
                "context": ctx,
                "tokens_est": _est_tokens(ctx),
                "as_of": as_of,
                "redacted_sensitive": redact_sensitive,
            }
            if answer:
                # full-context baseline for the same time view: what it would cost to stuff every eligible
                # episode into the prompt. For as-of reads, future episodes are not part of the baseline.
                full = "\n".join(
                    ep.content for ep in mem.episodes_doc.values()
                    if as_of is None or ep.event_time <= as_of
                )
                out["full_tokens"] = _est_tokens(full)
                out["answer"] = _answer_from_memory(self.answerer, query, ctx)
            # live token-saving accounting (Bet D online): context size always; the full-history baseline
            # only when this call computed it anyway — measuring must not add cost to the hot path.
            self.metrics.tokens(out["tokens_est"], out.get("full_tokens"))
            return out
        res = mem.search(query, user_id=user, as_of=as_of)
        visible_facts = [
            f for f in res.facts[:10]
            if not redact_sensitive or not getattr(f, "sensitive", False)
        ]
        answer = (
            res.answer()
            if not redact_sensitive
            else (visible_facts[0].object or visible_facts[0].text) if visible_facts else "I don't have that in memory."
        )
        return {
            "answer": answer,
            "facts": [f.text for f in visible_facts],
            "as_of": as_of,
            "redacted_sensitive": redact_sensitive,
        }

    @timed("recall_multi")
    def recall_multi(self, spaces: list[str], query: str, n_chunks: int = 6,
                     session_id: Optional[str] = None, as_of: Optional[float] = None,
                     redact_sensitive: bool = False) -> dict:
        """Read across several spaces and fuse the result (CLAUDE.md §6 — agent + team-shared + user memory
        composed at READ time). Each space contributes its own lean context, tagged with its source so
        provenance is preserved. Cross-space GRAPH walks are NOT done (entities live in per-space stores);
        this is fact+chunk composition, which is where the multi-scope value is — the multi-hop planner
        still operates within a single space."""
        blocks: list[str] = []
        for s in spaces:
            mem = self.get(s)
            ctx = mem.lean_context(query, user_id=s, n_chunks=n_chunks, session_id=session_id,
                                   as_of=as_of, redact_sensitive=redact_sensitive)
            if ctx.strip():
                blocks.append(f"## Space: {s}\n{ctx}")
        context = "\n\n".join(blocks)
        out = {"context": context, "tokens_est": _est_tokens(context), "spaces": list(spaces),
               "as_of": as_of, "redacted_sensitive": redact_sensitive}
        self.metrics.tokens(out["tokens_est"])
        return out

    def structured_profile(self, user: str) -> dict:
        """L2 structured profile (basic info / preferences / habits, confirmed vs tentative). Display-only."""
        return self.get(user).structured_profile(user)

    # --- suspected conflicts (LLM-detected in System-2, user-confirmed; never auto-resolved) --------
    def conflicts(self, user: str) -> dict:
        """Suspected conflicts awaiting the user's decision. Texts are localized for display."""
        from .localize import display_of

        mem = self.get(user)

        def disp(fid: str, fallback: str) -> str:
            f = mem.fact_store.get(fid) or mem.cold_store.get(fid)
            return display_of(f) if f is not None else fallback

        return {"conflicts": [{
            "id": c.id, "older": c.older, "newer": c.newer,
            "older_text": disp(c.older, c.text_older), "newer_text": disp(c.newer, c.text_newer),
            "reason": c.reason,
        } for c in mem.pending_conflicts(user)]}

    def resolve_conflict(self, user: str, conflict_id: str, keep: str = "newer") -> dict:
        """Apply the user's decision: keep newer/older (supersede the other) or both (dismiss). The user
        is authoritative — we never silently overwrite; this is the human-confirmed end of the loop."""
        with self.write_lock(user):
            mem = self.get(user)
            ok = (mem.dismiss_conflict(conflict_id) if keep == "both"
                  else mem.resolve_conflict(conflict_id, keep=keep))
            self._save(user, mem)
            return {"ok": ok}

    # --- working memory (ephemeral, session/TTL-scoped; feature ①) ----------
    def add_working(self, user: str, content: str, session_id: str = "default", kind: str = "state",
                    ttl_seconds: Optional[float] = None) -> dict:
        with self.write_lock(user):
            mem = self.get(user)
            wm = mem.remember_working(content, user_id=user, session_id=session_id, kind=kind,
                                      ttl_seconds=ttl_seconds)
            self._save(user, mem)
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
        with self.write_lock(user):
            mem = self.get(user)
            n = mem.clear_session(user, session_id)
            self._save(user, mem)
            return {"ok": True, "cleared": n}

    def close_session(
        self,
        user: str,
        session_id: str,
        summarize: bool = True,
        clear_working: bool = True,
    ) -> dict:
        """End-of-session grooming for agent clients.

        This does not delete the raw transcript. It drains pending System-2 work for the named session,
        optionally indexes session summaries for future lean reads, reflects knowledge updates into those
        summaries, clears ephemeral working memory, and persists the namespace. It is the lifecycle hook
        Claude Code / Codex / Cursor-style clients can call when a thread closes or switches tasks.
        """
        with self.write_lock(user):
            mem = self.get(user)
            canonical = mem.resolver.resolve(user)
            episodes = [
                ep for ep in mem.episodes_doc.values()
                if ep.user_id == canonical and ep.session_id == session_id
            ]
            pending = [ep for ep in episodes if not ep.consolidated]
            stats = (
                mem.consolidate(pending)
                if pending
                else {"facts_added": 0, "duplicates": 0, "invalidated": 0}
            )
            summaries = mem.summarize_episodes(episodes) if summarize else 0
            reflected = mem.reflect(user) if summarize else 0
            working_cleared = mem.clear_session(user, session_id) if clear_working else 0
            self._save(user, mem)
            return {
                "ok": True,
                "session_id": session_id,
                "episodes": len(episodes),
                "pending_consolidated": len(pending),
                "facts_added": stats.get("facts_added", 0),
                "duplicates": stats.get("duplicates", 0),
                "invalidated": stats.get("invalidated", 0),
                "summaries": summaries,
                "reflected": reflected,
                "working_cleared": working_cleared,
            }

    def profile(self, user: str) -> dict:
        mem = self.get(user)
        return {"profile": mem.build_persona(user),
                "facts": [f.text for f in _all_facts(mem) if f.is_live()][:50]}

    def get_focus(self, user: str) -> dict:
        return self.get(user).get_focus()

    def get_policy(self, user: str) -> dict:
        return self.get(user).get_policy()

    def agent_status(self, user: str, session_id: Optional[str] = None) -> dict:
        """Content-free state an agent can inspect before using Engram.

        Unlike recall/profile/memories, this intentionally avoids profile prose, fact text, raw episodes,
        summaries, and storage paths. It answers: "which namespace/session am I attached to, is there
        working state for this session, what is the focus policy, and what should I do next?"
        """
        mem = self.get(user)
        canonical = mem.resolver.resolve(user)
        stats = self.stats(user)
        session_episodes = [
            ep for ep in mem.episodes_doc.values()
            if ep.user_id == canonical and (session_id is None or ep.session_id == session_id)
        ]
        session_working = [
            w for w in mem.working_mem.values()
            if w.user_id == canonical
            and w.is_live()
            and (session_id is None or w.session_id == session_id)
        ]
        pending_session_episodes = [ep for ep in session_episodes if not ep.consolidated]
        focus = mem.get_focus()
        next_actions = [
            "Call engram_recall before answering tasks that depend on prior user/project context.",
            "Call engram_remember(scope='long' or 'auto') for durable preferences, decisions, and reusable facts.",
            "Use scope='working' for current-task state that should be cleared when the session closes.",
            "Call engram_close_session when this thread ends or switches tasks.",
        ]
        if (stats.get("counts") or {}).get("pending_conflicts", 0):
            next_actions.append("Review pending conflicts before relying on disputed memories.")
        return {
            "ok": True,
            "user": user,
            "session_id": session_id,
            "mode": "content_free_agent_status",
            "focus": focus,
            "session": {
                "id": session_id,
                "episodes": len(session_episodes),
                "episodes_pending": len(pending_session_episodes),
                "working_live": len(session_working),
            },
            "counts": stats.get("counts", {}),
            "consolidation_backlog": stats.get("consolidation_backlog", False),
            "storage": stats.get("storage"),
            "embedder": stats.get("embedder"),
            "llm_configured": stats.get("llm_configured"),
            "recommended_next_actions": next_actions,
            "tools": {
                "read_context": "engram_recall",
                "write_memory": "engram_remember",
                "close_session": "engram_close_session",
                "inspect_facts": "engram_list_facts",
                "correct_fact": "engram_update_fact",
                "delete_fact": "engram_delete_fact",
                "focus": "engram_get_focus / engram_set_focus",
            },
        }

    def session_report(
        self,
        user: str,
        session_id: str,
        include_sensitive: bool = False,
    ) -> dict:
        """Audit what a specific session contributed to memory.

        This is an explicit inspection endpoint, unlike `agent_status`. It may return fact text, but
        sensitive facts are redacted by default so an agent can safely show a post-session audit without
        accidentally surfacing private health/finance/PII details.
        """
        from .localize import display_of

        mem = self.get(user)
        canonical = mem.resolver.resolve(user)
        episodes = [
            ep for ep in mem.episodes_doc.values()
            if ep.user_id == canonical and ep.session_id == session_id
        ]
        episode_ids = {ep.id for ep in episodes}
        pending = [ep for ep in episodes if not ep.consolidated]
        session_facts = [
            f for f in _all_facts(mem)
            if f.user_id == canonical and episode_ids.intersection(f.provenance)
        ]
        session_facts.sort(key=lambda f: f.valid_at, reverse=True)
        redacted = 0

        def fact_view(f) -> dict:
            nonlocal redacted
            is_sensitive = bool(getattr(f, "sensitive", False))
            hidden = is_sensitive and not include_sensitive
            if hidden:
                redacted += 1
            base = {
                "id": f.id,
                "valid_at": fmt_datetime(f.valid_at),
                "invalid_at": fmt_datetime(f.invalid_at) if f.invalid_at else None,
                "status": "live" if f.is_live() else "superseded",
                "source": f.source,
                "category": getattr(f, "category", ""),
                "sensitive": is_sensitive,
                "redacted": hidden,
                "provenance": [ep for ep in f.provenance if ep in episode_ids],
            }
            if hidden:
                return {**base, "text": "[redacted sensitive fact]", "display": "[redacted sensitive fact]"}
            return {
                **base,
                "text": f.text,
                "display": display_of(f),
                "subject": f.subject,
                "predicate": f.predicate,
                "object": f.object,
            }

        facts_payload = [fact_view(f) for f in session_facts]
        working_live = [
            w for w in mem.working_mem.values()
            if w.user_id == canonical and w.session_id == session_id and w.is_live()
        ]
        return {
            "ok": True,
            "user": user,
            "session_id": session_id,
            "include_sensitive": include_sensitive,
            "episodes": len(episodes),
            "episodes_consolidated": len([ep for ep in episodes if ep.consolidated]),
            "episodes_pending": len(pending),
            "working_live": len(working_live),
            "facts_added": len(session_facts),
            "facts_redacted": redacted,
            "facts": facts_payload,
        }

    def sessions(
        self,
        user: str,
        limit: Optional[int] = None,
        offset: int = 0,
        q: str = "",
    ) -> dict:
        """Content-free session index for cross-agent memory management.

        This intentionally returns counts and timestamps, not raw episode text, summaries, profile prose,
        or fact text. A product can list Codex/Claude/app sessions first, then call `session_report()` only
        when the user chooses to audit a specific session.
        """
        mem = self.get(user)
        canonical = mem.resolver.resolve(user)
        sessions: dict[str, dict] = {}

        def ensure(sid: str) -> dict:
            if sid not in sessions:
                sessions[sid] = {
                    "id": sid,
                    "episodes": 0,
                    "episodes_consolidated": 0,
                    "episodes_pending": 0,
                    "facts_added": 0,
                    "facts_sensitive": 0,
                    "working_live": 0,
                    "summaries": 0,
                    "first_event_at": None,
                    "first_event_at_h": None,
                    "last_event_at": None,
                    "last_event_at_h": None,
                }
            return sessions[sid]

        episode_ids_by_session: dict[str, set[str]] = {}
        for ep in mem.episodes_doc.values():
            if ep.user_id != canonical:
                continue
            row = ensure(ep.session_id)
            row["episodes"] += 1
            if ep.consolidated:
                row["episodes_consolidated"] += 1
            else:
                row["episodes_pending"] += 1
            if ep.summary:
                row["summaries"] += 1
            first = row["first_event_at"]
            last = row["last_event_at"]
            if first is None or ep.event_time < first:
                row["first_event_at"] = ep.event_time
                row["first_event_at_h"] = fmt_datetime(ep.event_time)
            if last is None or ep.event_time > last:
                row["last_event_at"] = ep.event_time
                row["last_event_at_h"] = fmt_datetime(ep.event_time)
            episode_ids_by_session.setdefault(ep.session_id, set()).add(ep.id)

        for w in mem.working_mem.values():
            if w.user_id == canonical and w.is_live():
                ensure(w.session_id)["working_live"] += 1

        if episode_ids_by_session:
            for f in _all_facts(mem):
                if f.user_id != canonical:
                    continue
                provenance = set(f.provenance)
                if not provenance:
                    continue
                for sid, episode_ids in episode_ids_by_session.items():
                    if provenance.intersection(episode_ids):
                        row = ensure(sid)
                        row["facts_added"] += 1
                        if getattr(f, "sensitive", False):
                            row["facts_sensitive"] += 1

        needle = q.strip().lower()
        rows = list(sessions.values())
        if needle:
            rows = [row for row in rows if needle in row["id"].lower()]
        rows.sort(key=lambda row: row["last_event_at"] or 0, reverse=True)
        lim = _clamp_limit(limit, default=None, max_value=500)
        page_items, page = _page(rows, offset, lim)
        return {
            "ok": True,
            "user": user,
            "sessions": page_items,
            "page": {**page, "items": page_items},
            "next_offset": page["next_offset"],
        }

    def graph(
        self,
        user: str,
        as_of: float | None = None,
        include_sensitive: bool = False,
        q: str = "",
        live_only: bool = False,
        limit: Optional[int] = None,
    ) -> dict:
        lim = _clamp_limit(limit, default=None, max_value=500)
        return self.get(user).graph_data(
            user,
            as_of=as_of,
            include_sensitive=include_sensitive,
            q=q,
            live_only=live_only,
            limit=lim,
        )

    def memories(
        self,
        user: str,
        facts_limit: Optional[int] = None,
        facts_offset: int = 0,
        episodes_limit: Optional[int] = None,
        episodes_offset: int = 0,
        status: Optional[str] = None,
        q: str = "",
        include_sensitive: bool = False,
    ) -> dict:
        """Browse stored memory.

        Default is share-safe: non-sensitive facts plus content-free counts, with profile/raw episodes
        omitted. With `include_sensitive=True`, this is the owner-visible inspection payload: profile,
        raw episodes + L2 summaries, and sensitive facts.
        """
        from .localize import display_of  # localized rendering for Chinese-recorded facts

        mem = self.get(user)
        all_facts = sorted(_all_facts(mem), key=lambda f: f.valid_at, reverse=True)
        episodes_all = sorted(mem.episodes_doc.values(), key=lambda ep: ep.event_time, reverse=True)

        needle = q.strip().lower()

        def fact_view(f) -> dict:
            return {
                "id": f.id, "text": f.text, "display": display_of(f),
                "subject": f.subject, "predicate": f.predicate,
                "object": f.object, "valid_at": fmt_datetime(f.valid_at),
                "invalid_at": fmt_datetime(f.invalid_at) if f.invalid_at else None,
                "status": "live" if f.is_live() else "superseded",
                "source": f.source, "supersedes": f.supersedes,
                "category": getattr(f, "category", ""), "sensitive": getattr(f, "sensitive", False),
                "salience": round(f.salience, 2), "provenance": f.provenance,
            }

        def episode_view(ep) -> dict:
            return {
                "date": ep.metadata.get("date") or fmt_date(ep.event_time),
                "session": ep.session_id,
                "content": ep.content[:500],
                "summary": ep.summary,
            }

        filtered_facts = all_facts
        if status in {"live", "current"}:
            filtered_facts = [f for f in filtered_facts if f.is_live()]
        elif status in {"superseded", "old", "history"}:
            filtered_facts = [f for f in filtered_facts if not f.is_live()]
        if not include_sensitive:
            filtered_facts = [f for f in filtered_facts if not getattr(f, "sensitive", False)]
        if needle:
            def matches_fact(f) -> bool:
                haystack = " ".join([
                    f.text,
                    display_of(f),
                    f.subject,
                    f.predicate,
                    f.object,
                    getattr(f, "category", ""),
                ]).lower()
                return needle in haystack
            filtered_facts = [f for f in filtered_facts if matches_fact(f)]

        filtered_episodes = [] if not include_sensitive else episodes_all
        if needle and include_sensitive:
            filtered_episodes = [
                ep for ep in filtered_episodes
                if needle in " ".join([ep.content or "", ep.summary or "", ep.session_id or ""]).lower()
            ]

        facts_lim = _clamp_limit(facts_limit, default=None, max_value=1000)
        episodes_lim = _clamp_limit(episodes_limit, default=None, max_value=200)
        facts_page_items, facts_page = _page(filtered_facts, facts_offset, facts_lim)
        episodes_page_items, episodes_page = _page(filtered_episodes, episodes_offset, episodes_lim)
        facts_payload = [fact_view(f) for f in facts_page_items]
        episodes_payload = [episode_view(ep) for ep in episodes_page_items]
        return {
            "user": user,
            "profile": mem.build_persona(user) if include_sensitive else "",
            "counts": {"episodes": len(episodes_all),
                       "facts_live": sum(1 for f in all_facts if f.is_live()),
                       "facts_superseded": sum(1 for f in all_facts if not f.is_live()),
                       "summaries": len(mem.summary_vec.values())},
            "facts": facts_payload,
            "episodes": episodes_payload,
            "facts_page": {**facts_page, "items": facts_payload},
            "episodes_page": {**episodes_page, "items": episodes_payload},
            "next_offsets": {
                "facts": facts_page["next_offset"],
                "episodes": episodes_page["next_offset"],
            },
        }

    def stats(self, user: str) -> dict:
        """Content-free namespace stats for dashboards/readiness probes. This intentionally avoids
        profile text, fact text, episode snippets, and data paths so it is safe to poll in production."""
        mem = self.get(user)
        episodes = [ep for ep in mem.episodes_doc.values() if ep.user_id == user]
        hot_facts = [f for f in mem.fact_store.values() if f.user_id == user]
        cold_facts = [f for f in mem.cold_store.values() if f.user_id == user]
        facts = hot_facts + cold_facts
        facts_by_id = {f.id: f for f in facts}
        working = [w for w in mem.working_mem.values() if w.user_id == user]
        live_facts = [f for f in facts if f.is_live()]
        superseded = [f for f in facts if not f.is_live()]
        sensitive = [f for f in facts if getattr(f, "sensitive", False)]
        pending_conflicts = [
            c for c in mem.conflicts.values()
            if c.user_id == user and c.status == "pending"
        ]
        consolidated_episodes = [ep for ep in episodes if ep.consolidated]
        pending_episodes = [ep for ep in episodes if not ep.consolidated]
        ephemeral_episodes = [ep for ep in episodes if ep.metadata.get("ephemeral")]
        event_times = [ep.event_time for ep in episodes]
        fact_times = [f.valid_at for f in facts]
        user_entities = [e for e in mem.graph.entities.values() if e.user_id == user]
        all_relations = mem.graph.relations()
        user_entity_ids = {e.id for e in user_entities}
        user_relations = [
            r for r in all_relations
            if r.subject_id in user_entity_ids or r.object_id in user_entity_ids
        ]
        referenced_entity_ids = {
            eid for r in user_relations for eid in (r.subject_id, r.object_id)
        }
        stale_relations = [r for r in user_relations if r.fact_id not in facts_by_id]
        return {
            "user": user,
            "counts": {
                "episodes": len(episodes),
                "episodes_consolidated": len(consolidated_episodes),
                "episodes_pending": len(pending_episodes),
                "episodes_ephemeral": len(ephemeral_episodes),
                "facts_hot": len(hot_facts),
                "facts_cold": len(cold_facts),
                "cold_pages_out": int(mem.cold_pages_out.get(user, 0)),
                "cold_pages_in": int(mem.cold_pages_in.get(user, 0)),
                "facts_live": len(live_facts),
                "facts_superseded": len(superseded),
                "facts_sensitive": len(sensitive),
                "working_live": sum(1 for w in working if w.is_live()),
                "summaries": len([s for s in mem.summary_vec.values() if s.user_id == user]),
                "entities": len(user_entities),
                "relations": sum(1 for r in user_relations if r.fact_id in facts_by_id),
                "graph_orphan_entities": sum(1 for e in user_entities if e.id not in referenced_entity_ids),
                "graph_stale_relations": len(stale_relations),
                "pending_conflicts": len(pending_conflicts),
            },
            "time_range": {
                "first_event_at": min(event_times) if event_times else None,
                "first_event_at_h": fmt_datetime(min(event_times)) if event_times else None,
                "last_event_at": max(event_times) if event_times else None,
                "last_event_at_h": fmt_datetime(max(event_times)) if event_times else None,
                "oldest_fact_valid_at": min(fact_times) if fact_times else None,
                "oldest_fact_valid_at_h": fmt_datetime(min(fact_times)) if fact_times else None,
                "newest_fact_valid_at": max(fact_times) if fact_times else None,
                "newest_fact_valid_at_h": fmt_datetime(max(fact_times)) if fact_times else None,
            },
            "storage": self.config.storage,
            "max_hot_facts": self.config.max_hot_facts,
            "embedder": self.embedder.__class__.__name__,
            "llm_configured": self.llm is not None,
            "answerer_configured": self.answerer is not None,
            "consolidation_backlog": bool(pending_episodes),
        }

    def export(self, user: str, include_sensitive: bool = False) -> dict:
        """Data export.

        Default is a share-safe structured export: only non-sensitive facts plus a graph derived from
        those facts. Free-text layers (profile, raw episodes, summaries) are omitted because they can
        fold sensitive content into prose. With `include_sensitive=True`, this is full-fidelity
        portability: every fact's bi-temporal stamps + provenance, raw episodes, summaries, profile,
        focus, and graph.
        """
        mem = self.get(user)
        _facts = [f for f in _all_facts(mem)
                  if include_sensitive or not getattr(f, "sensitive", False)]
        graph = mem.graph_data(user, include_sensitive=include_sensitive)
        out = {
            "engram_export_version": 1,
            "user": user,
            "include_sensitive": include_sensitive,
            "redacted_sensitive": not include_sensitive,
            "profile": mem.build_persona(user) if include_sensitive else "",
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
            "episodes": [] if not include_sensitive else [{
                "id": ep.id, "session_id": ep.session_id, "speaker": ep.speaker,
                "event_time": ep.event_time, "date": ep.metadata.get("date") or fmt_date(ep.event_time),
                "content": ep.content, "summary": ep.summary,
            } for ep in mem.episodes_doc.values()],
            "graph": graph,
        }
        if not include_sensitive:
            out["redaction_note"] = (
                "Sensitive facts and all free-text layers (profile, raw episodes, summaries) were omitted."
            )
        return out
