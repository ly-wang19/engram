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
from .store.persist import DimensionMismatchError, EmbedderMismatchError
from .util import fmt_date, fmt_datetime

DEFAULT_DATA_DIR = os.path.expanduser("~/.engram/data")

# _BASIC normalized fields that legitimately hold several values at once. The audit's slot_overflow
# pre-pass ("a single-valued slot cannot hold N values") is only true of the others.
_MULTI_VALUED_FIELDS = {"children", "language", "education"}

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
        # Operator kill switch. Session outcomes are the only default that spends an LLM call per session
        # close, and a deployed server cannot control what its clients pass in the request body — so the
        # cost ceiling has to be settable server-side, not per-request. Which means it cannot be only a
        # DEFAULT: /v1/sessions/close takes an `outcomes` field, and this repo's own watcher
        # (connectors/watch.py) posts `outcomes: True` on every session it imports, so a switch that only
        # moved the default would leave the operator's cost ceiling off for the one caller that runs
        # unattended. Keep the flag and enforce it in close_session as an override.
        self._outcomes_forced_off = os.environ.get("ENGRAM_SESSION_OUTCOMES") == "0"
        if self._outcomes_forced_off:
            self.config.session_outcomes = False
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
        try:
            mem = Memory.open(self._path(user), embedder=self.embedder, llm=self.llm, config=self.config)
        except (DimensionMismatchError, EmbedderMismatchError):
            # The store's vectors are from a different embedder (ENGRAM_EMBEDDER changed). Refusing is
            # the safe default (mixing spaces silently corrupts retrieval); with the opt-in flag we
            # migrate in place on first touch — re-embed everything from text and persist, after which
            # the manifest matches the new embedder.
            if os.environ.get("ENGRAM_REEMBED_ON_MISMATCH") != "1":
                raise
            with self._user_file_lock(user):
                mem = Memory.open(self._path(user), allow_mismatch=True,
                                  embedder=self.embedder, llm=self.llm, config=self.config)
                mem.reembed()
                mem.save()
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
                data: Any = None, consolidate: Optional[bool] = None, summarize: bool = True,
                session_id: str = "imported", dedupe: bool = True) -> dict:
        """Bulk import: either pre-parsed `sessions` (list of ImportSession/dicts) OR raw `data` to parse
        with `format` (chatgpt/messages/records/jsonl/transcript/auto). One batched ingest + consolidation.
        Idempotent by default: re-posting the same export skips already-ingested episodes (`skipped`).
        `consolidate=None` resolves inside Memory.import_messages: per-turn extraction for every source
        except agent-session transcripts, which are stored for close-time distillation instead."""
        with self.write_lock(user):
            mem = self.get(user)
            if sessions is None:
                from .connectors import load_json, parse
                # An Engram export snapshot restores directly (facts keep their bi-temporal stamps;
                # nothing is re-extracted) — route it before the message-history parsers, which would
                # otherwise mis-sniff the dict as 'records' and reject it.
                obj = data
                if isinstance(obj, (str, bytes, bytearray)):
                    try:
                        obj = load_json(obj)
                    except Exception:  # noqa: BLE001 — plain text (transcript etc.): old path below
                        obj = data
                if isinstance(obj, dict) and "engram_export_version" in obj \
                        and format in ("auto", "engram"):
                    stats = mem.import_snapshot(obj, user_id=user, dedupe=dedupe)
                    self._save(user, mem)
                    return {"ok": True, **stats}
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
            # Report `source` back: an edit makes the fact user-authored, and that is precisely what a
            # caller needs to confirm — without it there is no way to tell that the correction will
            # survive the next extraction pass rather than being silently overwritten.
            return {"ok": True, "id": f.id, "text": f.text, "source": f.source}

    def delete_fact(self, user: str, fact_id: str) -> dict:
        with self.write_lock(user):
            mem = self.get(user)
            ok = mem.delete_fact(fact_id)
            self._save(user, mem)
            return {"ok": ok}

    def clear_slot(self, user: str, subject: str, predicate: str, expect_count: int) -> dict:
        """Hard-delete every fact on one (subject, predicate) slot.

        The audit's slot_overflow finding tells the owner to clear the slot; without this the console is
        a diagnosis with no cure — on a real store that meant 84 rows behind 84 separate confirm dialogs.
        Erasure rather than invalidation is right here: a slot that holds 84 values never held a claim
        that was true and later stopped being true, so there is no history worth preserving. That is the
        same right-to-forget path `delete_fact` already models.

        `expect_count` is a required optimistic-concurrency guard, not an option. The owner approves
        deleting the N rows the audit showed them; if the store moved in between (a watcher run, another
        session), refusing is the only safe answer — silently erasing more than was approved is exactly
        the corruption CLAUDE.md §8 forbids. Making it optional would leave the unguarded call as the
        API's default shape, which is the one shape this operation must not have.
        """
        with self.write_lock(user):
            mem = self.get(user)
            canonical = mem.resolver.resolve(user)
            pred = predicate.strip().lower()
            # The doomed set must be EXACTLY the set audit() grouped, or the count guard compares two
            # different populations and passes while erasing the wrong rows:
            #   * resolve BOTH sides of the identity check. Facts written before an identity link carry
            #     the raw handle and facts written after carry the canonical one; comparing a stored
            #     `user_id` against one spelling silently targets the other half of the same person.
            #   * live facts only. audit() groups live facts, so `expect_count` is a live count; matching
            #     superseded rows here would both break the guard permanently on any slot that has a
            #     supersedes chain and, when the guard is omitted, hard-delete bi-temporal history the
            #     owner was never shown (CLAUDE.md §3.1: invalidate, never erase, a superseded fact).
            #   * skip source="user". A fact the owner typed is not extraction junk, and audit() leaves
            #     it out of the group for the same reason.
            doomed = [f for f in _all_facts(mem)
                      if f.is_live() and f.source != "user"
                      and mem.resolver.resolve(f.user_id) == canonical
                      and f.subject == subject and f.predicate.strip().lower() == pred]
            if len(doomed) != expect_count:
                return {"ok": False, "deleted": 0, "found": len(doomed),
                        "expected": expect_count, "reason": "slot changed since it was audited"}
            for f in doomed:
                mem.delete_fact(f.id)
            self._save(user, mem)
            return {"ok": True, "deleted": len(doomed), "subject": subject, "predicate": predicate}

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
        outcomes: Optional[bool] = None,
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

            # Session end is when a conclusion exists to record: mid-session there is only a task in
            # progress. This is the write that keeps a personal memory fed — the per-turn extractor
            # yields biographical triples, which a working session simply does not contain.
            outcome_facts = 0
            want = self.config.session_outcomes if outcomes is None else outcomes
            if self._outcomes_forced_off:
                want = False  # ENGRAM_SESSION_OUTCOMES=0 is the operator's ceiling; no request body lifts it
            if want and self.llm is not None and episodes:
                from .consolidate.outcomes import (
                    OUTCOME_PREDICATES, extract_outcomes, split_outcome_text,
                )

                # Closing the same session twice must not double the conclusions. extract_outcomes only
                # dedupes within one call, and re-closing is the normal case: connectors/watch.py
                # re-sends any transcript that grew and closes it again. Same 120-char key it uses.
                seen_keys = {
                    split_outcome_text(f.text)[0].lower()[:120]
                    for f in _all_facts(mem)
                    if f.is_live() and f.user_id == canonical
                    and f.predicate.lower() in OUTCOME_PREDICATES and f.subject == session_id
                }
                for fact in extract_outcomes(self.llm, episodes, canonical, session_id=session_id):
                    if split_outcome_text(fact.text)[0].lower()[:120] in seen_keys:
                        continue
                    fact.embedding = mem.embedder.embed(fact.text)
                    mem.fact_store.upsert(fact.id, fact.embedding, fact)
                    mem.engine.graph_builder.add_fact(fact)
                    outcome_facts += 1
                if outcome_facts:
                    mem._persona_cache.clear()

            self._save(user, mem)
            return {
                "ok": True,
                "session_id": session_id,
                "episodes": len(episodes),
                "pending_consolidated": len(pending),
                "facts_added": stats.get("facts_added", 0),
                "outcomes": outcome_facts,
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
        blind = self._embedder_blindness(mem, canonical)
        if blind:
            next_actions.append(
                f"{round(blind['ratio'] * 100)}% of this namespace's memories cannot be ranked by the "
                f"configured embedder ({blind['embedder']}); recall will return the same items for "
                f"unrelated queries. Ask the operator to run: {blind['migrate']}")
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
            "feed": stats.get("feed"),
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
        kind: Optional[str] = None,
        q: str = "",
        include_sensitive: bool = False,
    ) -> dict:
        """Browse stored memory.

        Default is share-safe: non-sensitive facts plus content-free counts, with profile/raw episodes
        omitted. With `include_sensitive=True`, this is the owner-visible inspection payload: profile,
        raw episodes + L2 summaries, and sensitive facts.

        `kind` splits the fact set into the two things a reader actually browses separately: 'outcomes'
        (what sessions concluded) vs 'attributes' (everything else). Unknown values are ignored rather
        than rejected, like `status`.
        """
        from .consolidate.outcomes import OUTCOME_PREDICATES, split_outcome_text
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
                # An outcome's `text` is "statement （依据：why）" — one embedded string, because that is
                # what recall matches. A reader wants the two apart, so split once here instead of
                # asking every surface to re-parse the separator. Always present, "" for attributes.
                "why": split_outcome_text(f.text)[1]
                       if f.predicate.lower() in OUTCOME_PREDICATES else "",
            }

        def episode_view(ep) -> dict:
            return {
                # Facts carry provenance as episode ids; without the id here a caller holding a fact
                # cannot resolve "where did this come from?" — which is the whole point of provenance.
                "id": ep.id,
                "date": ep.metadata.get("date") or fmt_date(ep.event_time),
                "session": ep.session_id,
                # Full text, not a 500-char stub. This view is how the owner answers "what did I
                # actually write?" — the one thing a plain .md file always got right and this did not.
                # Callers that want a preview slice it themselves; callers that want the record need it
                # whole. Response size is already bounded by episodes_limit/episodes_offset paging.
                "content": ep.content,
                "chars": len(ep.content),
                "summary": ep.summary,
            }

        filtered_facts = all_facts
        if status in {"live", "current"}:
            filtered_facts = [f for f in filtered_facts if f.is_live()]
        elif status in {"superseded", "old", "history"}:
            filtered_facts = [f for f in filtered_facts if not f.is_live()]
        # Facts only: episodes have no predicate, so `kind` never touches filtered_episodes.
        kind_key = (kind or "").strip().lower()
        if kind_key == "outcomes":
            filtered_facts = [f for f in filtered_facts if f.predicate.lower() in OUTCOME_PREDICATES]
        elif kind_key == "attributes":
            filtered_facts = [f for f in filtered_facts if f.predicate.lower() not in OUTCOME_PREDICATES]
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
                       # Over the UNFILTERED set, like facts_live/facts_superseded: a count that shrank
                       # with the current filter could not tell a caller whether outcomes exist at all.
                       "facts_outcomes": sum(1 for f in all_facts
                                             if f.is_live() and f.predicate.lower() in OUTCOME_PREDICATES),
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

    def _embedder_blindness(self, mem: Memory, canonical: str) -> Optional[dict]:
        """One store-level finding when the configured embedder cannot rank what this namespace holds.

        Measured on the owner's real store: HashingEmbedder scored 0.000 for every Chinese query/fact
        pair and /v1/recall returned the identical four facts for three unrelated queries
        (results/embedder_zh_2026-09-01.md). Nothing in the product said why. The cause is structural —
        the hashing stemmer only emits [a-z0-9] tokens, so a CJK-dominated text hashes to (almost)
        nothing and BM25 sees the same empty token stream — which makes it detectable without a model
        call: count how much of the corpus is non-ASCII. Shared by audit() and agent_status() so the
        console and the agent see the same verdict.
        """
        from .embed import HashingEmbedder

        embedder = self.embedder
        if isinstance(embedder, HashingEmbedder):
            exact = True  # the blindness is a property of the tokenizer, not a heuristic
        elif (embedder.__class__.__name__ == "SentenceTransformerEmbedder"
              and "-en-" in str(getattr(embedder, "model_name", "") or "")):
            exact = False  # an English-only model degrades on CJK rather than zeroing out
        else:
            return None

        def split(text: str) -> tuple[int, int]:
            """(rankable, dark) character counts. Rankable = ASCII alphanumerics, the only thing the
            hashing stemmer's [a-z0-9]+ tokenizer keeps; dark = letters in any other script, which it
            drops on the floor. Punctuation and whitespace are neither: a JSON blob or a shell line is
            100% rankable even though most of its characters are braces and pipes — the first version
            counted those against the text and called a store of code facts "33% unrankable"."""
            rankable = dark = 0
            for c in text:
                if c.isascii():
                    if c.isalnum():
                        rankable += 1
                elif c.isalpha():
                    dark += 1
            return rankable, dark

        def blind(text: str) -> bool:
            rankable, dark = split(text)
            return (rankable + dark) > 0 and dark / (rankable + dark) >= 0.5

        facts = [f for f in _all_facts(mem)
                 if mem.resolver.resolve(f.user_id) == canonical and f.is_live()]
        episodes = sorted((ep for ep in mem.episodes_doc.values() if ep.user_id == canonical),
                          key=lambda ep: ep.ingested_at, reverse=True)[:2000]
        facts_blind = sum(1 for f in facts if blind(f.text or ""))
        episodes_blind = sum(1 for ep in episodes if blind(ep.content or ""))
        checked = len(facts) + len(episodes)
        total_blind = facts_blind + episodes_blind
        # Gate on letter mass, not item count: a namespace holding two 120k-char Chinese transcripts is
        # entirely unrankable and must not stay silent for being "only 2 memories". 200 letters is a
        # paragraph — enough that a three-line store says nothing, little enough that a dozen one-line
        # notes do. (A dozen English facts with one CJK name stays far under the 20% share either way.)
        rankable_chars = dark_chars = 0
        for text in [f.text or "" for f in facts] + [ep.content or "" for ep in episodes]:
            r, d = split(text)
            rankable_chars += r
            dark_chars += d
        if rankable_chars + dark_chars < 200:
            return None
        ratio = total_blind / checked if checked else 0.0
        mass_ratio = dark_chars / (rankable_chars + dark_chars)
        if ratio < 0.20 and mass_ratio < 0.20:
            return None
        ratio = max(ratio, mass_ratio)
        name = embedder.__class__.__name__
        pct = round(ratio * 100)
        migrate = "ENGRAM_EMBEDDER=multilingual ENGRAM_REEMBED_ON_MISMATCH=1"
        return {
            "kind": "embedder_blind",
            "text": f"{name} cannot rank {pct}% of {checked} memories",
            "why": (f"{pct}% of what this namespace holds is written in a script {name} cannot "
                    f"tokenize — it only indexes [a-z0-9] tokens, so those memories are invisible to both "
                    f"the semantic and lexical channels and recall returns the same items for unrelated "
                    f"queries"),
            "action": (f"switch the server embedder and re-embed: {migrate} "
                       f"(or bge-small-zh, 92MB, if memory is tight)"),
            "embedder": name,
            "model": getattr(embedder, "model_name", None),
            "blind": total_blind, "checked": checked, "ratio": ratio,
            "dark_chars": dark_chars, "rankable_chars": rankable_chars,
            "facts_blind": facts_blind, "episodes_blind": episodes_blind,
            "exact": exact, "recommended": "multilingual", "alternative": "bge-small-zh",
            "migrate": migrate,
        }

    def audit(self, user: str, limit: int = 40) -> dict:
        """Memory health check: surface entries a person would want to fix, with the reason attached.

        The point of a personal memory store is not that it remembers a lot — it is that you can CHECK
        and CORRECT what it remembers. Extraction on real corpora reliably produces junk (snake_case
        tokens lifted from source text, facts whose object repeats the predicate, one-off noise that will
        never be recalled), and today none of it is visible unless you read the raw fact list. Each
        finding carries a `why` and a suggested `action` so the console can offer a fix, not just a count.
        """
        import re

        from .consolidate.outcomes import OUTCOME_PREDICATES
        from .consolidate.structured import _BASIC

        mem = self.get(user)
        # Resolve both sides: an owner whose handles were linked has facts stamped with either spelling,
        # and clear_slot() acts on exactly this set — the two must never disagree about who owns a fact.
        canonical = mem.resolver.resolve(user)
        facts = [f for f in _all_facts(mem)
                 if mem.resolver.resolve(f.user_id) == canonical and f.is_live()]
        graph = mem.graph_data(user, include_sensitive=True)
        referenced = {e["source"] for e in graph.get("edges", [])} | {e["target"] for e in graph.get("edges", [])}

        findings: list[dict] = []

        def add(kind: str, why: str, action: str, fact=None, entity: str = "") -> None:
            row = {"kind": kind, "why": why, "action": action}
            if fact is not None:
                row.update({"fact_id": fact.id, "text": fact.text, "subject": fact.subject,
                            "predicate": fact.predicate, "object": fact.object,
                            "source": fact.source, "valid_at": fact.valid_at,
                            "valid_at_h": fmt_date(fact.valid_at)})
            if entity:
                row["entity"] = entity
            findings.append(row)

        # Pre-pass: what is wrong with a store holding 84 live `occupation` facts is not any one row —
        # every row passes the per-fact rules — it is that a single-valued identity slot holds 84 values.
        # Only _BASIC predicates qualify: `likes` with 20 values is a correct list, `occupation` with 3
        # cannot be. Reported once per slot, and its facts skip the per-fact loop so one broken slot does
        # not bury every other finding under 84 duplicate rows.
        by_slot: dict[tuple[str, str], list] = {}
        for f in facts:
            # A fact the owner typed is by definition not extraction output, so it cannot be the junk
            # this rule names — and it is the most costly thing in the slot to lose to a bulk delete.
            # clear_slot() applies the same exclusion: the two must describe the same population or the
            # count guard compares one set while the delete runs on another.
            if f.source == "user":
                continue
            by_slot.setdefault((f.subject, f.predicate), []).append(f)
        overflowed: set[str] = set()
        for (subject, predicate), group in by_slot.items():
            if len(group) < 3 or predicate.lower() not in _BASIC:
                continue
            field, label = _BASIC[predicate.lower()]
            # ...and only the SINGLE-valued ones. _BASIC also normalizes children / speaks /
            # graduated_from, where three values is a person with three kids, three languages, three
            # degrees — not extraction junk. Telling the owner "单值属性不可能有 3 个值" about those is
            # false, and the finding carries a one-click irreversible delete.
            if field in _MULTI_VALUED_FIELDS:
                continue
            findings.append({
                "kind": "slot_overflow",
                # Prose in the same language as every other rule's; the console composes its own
                # localized sentence from count/predicate/label instead of rendering this one, because
                # a one-click irreversible delete must be explained in the reader's language.
                "why": f"{len(group)} facts all claim {label} ({predicate}) — a single-valued slot "
                       f"cannot have {len(group)} values; these are extraction fragments, not memories",
                "action": "look at the samples, then clear the slot",
                "subject": subject, "predicate": predicate, "label": label, "count": len(group),
                "samples": [{"fact_id": g.id, "text": g.text} for g in group[:5]],
            })
            overflowed.update(g.id for g in group)

        # Store-level, not per-fact: when the embedder cannot rank the corpus, every per-fact finding
        # is beside the point — the owner asked "why does recall return the same four facts" and the
        # answer is the embedder, reported once with the migration command attached.
        row = self._embedder_blindness(mem, canonical)
        if row:
            findings.append(row)

        for f in facts:
            # Outcome statements are 400-char sentences that routinely name a config path, so
            # unreduced_claim and code_artifact would flag most session conclusions as junk. They are
            # written whole by design; the per-fact rules exist to catch triples the extractor botched.
            if f.predicate.lower() in OUTCOME_PREDICATES or f.id in overflowed:
                continue
            obj, pred = f.object.strip(), f.predicate.strip()
            # A snake_case object is source text that was never turned into a value: "user interested in
            # kind_bar_fruit_nut_flavor" reads as machine output and matches no natural phrasing at recall.
            if "_" in obj and " " not in obj and len(obj) > 12:
                add("machine_token", f"object '{obj}' looks like a raw token, not a value",
                    "edit into natural wording, or delete if meaningless", f)
            # The object restating the predicate carries no information ("occupation: named Rex").
            elif obj.lower().replace("_", " ") == pred.lower().replace("_", " "):
                add("empty_value", f"object repeats the predicate ('{pred}')",
                    "delete: it asserts nothing", f)
            # A sentence-length object is a claim the extractor failed to reduce to a fact.
            elif len(obj) > 120:
                add("unreduced_claim", f"object is {len(obj)} chars — a sentence, not a value",
                    "shorten to the value, or delete", f)
            # Rules below come from auditing a real personal corpus (notes + technical docs), where the
            # offline extractor produced these shapes in bulk. Without them the check reports "all clear"
            # on a store that is visibly full of fragments.
            elif re.search(r'[)"\'`\]]\s*$|^[("\'`\[]', obj) or "`" in obj:
                add("fragment", f"object '{obj[:40]}' carries stray punctuation — a clipped sentence",
                    "rewrite as a plain value, or delete", f)
            elif re.search(r"[/\\]|\.(py|ts|md|json|yaml|sh)\b", obj):
                add("code_artifact", f"object '{obj[:40]}' looks like a path or code, not a memory",
                    "delete: source-code detail does not belong in a personal profile", f)
            elif len(obj.split()) <= 2 and obj.isupper():
                add("empty_value", f"object '{obj}' is a bare token with no statement around it",
                    "delete: it asserts nothing on its own", f)
            elif f.subject.lower() in {"this", "that", "it", "these", "those", "there", "here"}:
                add("dangling_subject", f"subject is the pronoun '{f.subject}' — no identifiable entity",
                    "delete, or rewrite naming who/what it refers to", f)

        for node in graph.get("nodes", []):
            if node.get("id") not in referenced:
                add("orphan_entity", f"entity '{node.get('name')}' has no relations",
                    "harmless, but it means no fact connects it", entity=node.get("name", ""))

        by_kind: dict[str, int] = {}
        for row in findings:
            by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + 1
        return {
            "user": user,
            "checked": {"facts": len(facts), "entities": len(graph.get("nodes", []))},
            "total_findings": len(findings),
            "by_kind": by_kind,
            "findings": findings[:limit],
            "truncated": len(findings) > limit,
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
        # Feed: is this memory being fed from the machine's agent sessions at all? Measured before this
        # existed: across 1909 of the owner's own sessions, remember/recall/close_session were called
        # zero times — an empty memory looks exactly like a healthy one unless something says when it
        # was last fed. Derived from episode metadata; nothing persisted.
        from .consolidate.outcomes import OUTCOME_PREDICATES

        fed = [ep for ep in episodes if ep.metadata.get("source") == "agent_session"]
        fed_sessions = {ep.session_id for ep in fed}
        last_fed_at = max((ep.ingested_at for ep in fed), default=None)
        feed = {
            "last_fed_at": last_fed_at,
            "last_fed_at_h": fmt_datetime(last_fed_at) if last_fed_at is not None else None,
            "sessions": len(fed_sessions),
            "conclusions": sum(1 for f in live_facts
                               if f.predicate.lower() in OUTCOME_PREDICATES and f.subject in fed_sessions),
        }
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
            "feed": feed,
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
