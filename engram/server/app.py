"""Engram Memory Service — a multi-tenant HTTP API so anyone can connect and manage their own memory
(CLAUDE.md §6, the serving/adoption layer). Each API key is an isolated memory namespace; memory persists
to disk between requests and restarts.

Run it:
    pip install "engram-memory[server]"
    export ENGRAM_API_KEYS="alice:sk-alice-123,bob:sk-bob-456"   # user:key pairs (or ENGRAM_OPEN=1 for open)
    export ENGRAM_EMBEDDER=bge-small        # local, no key
    export ENGRAM_LLM=deepseek              # optional: better fact extraction (needs the provider's key)
    uvicorn engram.server.app:app --host 0.0.0.0 --port 8000

Then a client (curl / SDK / the MCP bridge) calls it with `Authorization: Bearer <key>`:
    POST /v1/remember  {"content": "...", "session_id": "..."}   -> store + consolidate
    POST /v1/recall    {"query": "...", "lean": true}            -> a small retrieved context to answer from
    GET  /v1/profile                                             -> the user's synthesized profile
    POST /v1/forget                                              -> wipe this user's memory

This is the foundation of a hosted product (like Hy-Memory). It is single-node + file-backed by default —
swap the in-memory stores for pgvector/Qdrant + Kuzu (already pluggable behind the store interfaces) and
put it behind your auth/gateway to scale to a public service.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict
from typing import Optional

try:
    from fastapi import Depends, FastAPI, Header, HTTPException
    from pydantic import BaseModel
except Exception as exc:  # noqa: BLE001
    raise SystemExit("the server needs FastAPI — `pip install \"engram-memory[server]\"`") from exc

from ..memory import Memory

DATA_DIR = os.environ.get("ENGRAM_DATA_DIR", os.path.expanduser("~/.engram/data"))
MAX_HOT_USERS = int(os.environ.get("ENGRAM_MAX_HOT_USERS", "64"))  # LRU cap of in-RAM user memories


def _load_keys() -> dict[str, str]:
    """Map API key -> user_id from ENGRAM_API_KEYS ('alice:sk-a,bob:sk-b'). Empty => open mode."""
    out: dict[str, str] = {}
    for pair in os.environ.get("ENGRAM_API_KEYS", "").split(","):
        pair = pair.strip()
        if ":" in pair:
            user, key = pair.split(":", 1)
            out[key.strip()] = user.strip()
    return out


class MemoryManager:
    """Per-user persistent Memory with a shared embedder/LLM and an LRU of hot (in-RAM) users."""

    def __init__(self) -> None:
        from ..llm.providers import load_dotenv, make_embedder, make_llm

        load_dotenv()  # pick up provider keys (ARK_API_KEY, DEEPSEEK_API_KEY, ...) from a local .env
        os.makedirs(DATA_DIR, exist_ok=True)
        self.embedder = make_embedder(os.environ.get("ENGRAM_EMBEDDER", "bge-small"))
        llm_name = os.environ.get("ENGRAM_LLM", "")
        self.llm = make_llm(llm_name) if llm_name else None  # no LLM -> deterministic rule extractor
        self._hot: "OrderedDict[str, Memory]" = OrderedDict()
        self._locks: dict[str, threading.Lock] = {}
        self._g = threading.Lock()

    def _path(self, user: str) -> str:
        safe = "".join(c for c in user if c.isalnum() or c in "-_.") or "default"
        return os.path.join(DATA_DIR, f"{safe}.pkl")

    def lock(self, user: str) -> threading.Lock:
        with self._g:
            return self._locks.setdefault(user, threading.Lock())

    def get(self, user: str) -> Memory:
        with self._g:
            if user in self._hot:
                self._hot.move_to_end(user)
                return self._hot[user]
        mem = Memory.open(self._path(user), embedder=self.embedder, llm=self.llm)
        with self._g:
            self._hot[user] = mem
            self._hot.move_to_end(user)
            while len(self._hot) > MAX_HOT_USERS:
                self._hot.popitem(last=False)  # evict coldest user from RAM (its disk snapshot remains)
        return mem

    def forget(self, user: str) -> None:
        with self._g:
            self._hot.pop(user, None)
        p = self._path(user)
        if os.path.exists(p):
            os.remove(p)


app = FastAPI(title="Engram Memory Service", version="0.1.0",
              description="Multi-tenant long-term memory — connect with a Bearer key and manage your own memory.")
_mgr: Optional[MemoryManager] = None


def mgr() -> MemoryManager:
    global _mgr
    if _mgr is None:
        _mgr = MemoryManager()
    return _mgr


@app.on_event("startup")
def _warmup():
    mgr()  # load the embedder (and LLM) once at boot, so the first request doesn't race on it


def auth(authorization: str = Header(default="")) -> str:
    """Resolve the caller's user_id from the Bearer key. Open mode (no keys configured) uses the key text
    itself as the namespace, so anyone can try it without setup."""
    keys = _load_keys()
    token = authorization.replace("Bearer ", "").strip()
    if keys:
        if token not in keys:
            raise HTTPException(401, "invalid or missing API key")
        return keys[token]
    if os.environ.get("ENGRAM_OPEN") == "1":
        return token or "anonymous"
    raise HTTPException(401, "set ENGRAM_API_KEYS (user:key,...) or ENGRAM_OPEN=1")


class RememberReq(BaseModel):
    content: str
    session_id: str = "default"


class RecallReq(BaseModel):
    query: str
    lean: bool = True
    n_chunks: int = 6


@app.get("/health")
def health():
    return {"ok": True, "service": "engram", "users_hot": len(mgr()._hot)}


@app.post("/v1/remember")
def remember(req: RememberReq, user: str = Depends(auth)):
    m = mgr()
    with m.lock(user):
        mem = m.get(user)
        mem.add(req.content, user_id=user, session_id=req.session_id)
        # Consolidation/summarization use the LLM; make them BEST-EFFORT so a transient model outage never
        # loses the memory — the raw episode is already stored and recallable either way.
        added = 0
        try:
            added = mem.consolidate().get("facts_added", 0)         # extract facts + resolve conflicts
            mem.summarize_episodes(list(mem.episodes_doc.values()))  # L2 summaries for lean recall
        except Exception as exc:  # noqa: BLE001
            mem.save()
            return {"ok": True, "extracted": 0, "degraded": f"{type(exc).__name__}", "stored_raw": True}
        mem.save()
        return {"ok": True, "extracted": added,
                "total_facts": len([f for f in mem.fact_store.values() if f.is_live()])}


@app.post("/v1/recall")
def recall(req: RecallReq, user: str = Depends(auth)):
    mem = mgr().get(user)
    if req.lean:
        ctx = mem.lean_context(req.query, user_id=user, n_chunks=req.n_chunks)
        return {"context": ctx, "tokens_est": len(ctx.split())}
    res = mem.search(req.query, user_id=user)
    return {"answer": res.answer(), "facts": [f.text for f in res.facts[:10]]}


@app.get("/v1/profile")
def profile(user: str = Depends(auth)):
    mem = mgr().get(user)
    return {"profile": mem.build_persona(user),
            "facts": [f.text for f in mem.fact_store.values() if f.is_live()][:50]}


@app.post("/v1/forget")
def forget(user: str = Depends(auth)):
    mgr().forget(user)
    return {"ok": True, "message": f"all memory for '{user}' erased"}
