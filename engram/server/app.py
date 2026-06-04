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


@app.get("/")
def viewer():
    """A tiny built-in dashboard: enter your key, see/manage your own memory in the browser."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(_VIEWER_HTML)


_VIEWER_HTML = """<!DOCTYPE html><html lang=zh-CN><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Engram · 我的记忆</title><style>
*{box-sizing:border-box;margin:0;padding:0;font-family:"PingFang SC",system-ui,sans-serif}
body{background:#070b14;color:#eaf0ff;padding:24px;max-width:920px;margin:0 auto}
h1{font-size:22px;margin-bottom:4px}.dim{color:#8a97b8;font-size:13px}
.bar{display:flex;gap:8px;margin:18px 0}
input,button{padding:10px 14px;border-radius:10px;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.05);color:#eaf0ff;font-size:14px}
input{flex:1}button{cursor:pointer;background:linear-gradient(90deg,#22d3ee,#a78bfa);color:#04121a;font-weight:700;border:none}
.card{background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:16px;margin:12px 0}
.card h3{font-size:14px;color:#22d3ee;letter-spacing:.1em;text-transform:uppercase;margin-bottom:10px}
.f{padding:8px 0;border-bottom:1px solid rgba(255,255,255,.06);font-size:14.5px;display:flex;gap:10px;align-items:baseline}
.tg{font-size:11px;border-radius:6px;padding:2px 8px;white-space:nowrap}
.live{background:rgba(52,211,153,.16);color:#34d399}.old{background:rgba(255,255,255,.06);color:#8a97b8;text-decoration:line-through}
.dt{color:#22d3ee;font-size:11px;min-width:78px}.stat{display:inline-block;margin-right:16px;font-size:13px;color:#8a97b8}
.stat b{color:#eaf0ff;font-size:18px}pre{white-space:pre-wrap;font-size:13.5px;line-height:1.6}
.add{display:flex;gap:8px;margin-top:8px}.add input{flex:1}
.act{margin-left:auto;display:flex;gap:6px}.ib{font-size:12px;padding:3px 9px;border-radius:7px;cursor:pointer;border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.05);color:#eaf0ff}
.ib:hover{border-color:#22d3ee}.ib.del:hover{border-color:#fb7185;color:#fb7185}
.lock{font-size:11px;color:#fbbf24;margin-left:6px}
.addf{display:flex;gap:6px;margin-top:10px}.addf input{flex:1;font-size:13px;padding:7px 10px}
</style></head><body>
<h1>🧠 我的记忆 <span class=dim>Engram</span></h1>
<p class=dim>输入你的 API key,看 / 编辑 / 删除你自己的记忆。你手动改的会被锁定 🔒,不会被自动覆盖。</p>
<div class=bar><input id=key placeholder="API key(开放模式下随便填,比如 wei)" value="wei">
<button onclick=load()>查看我的记忆</button></div>
<div class=add><input id=msg placeholder="存一条新记忆,例如:我下周要去东京出差">
<button onclick=remember()>记住</button></div>
<div id=out></div>
<script>
const esc=s=>(s||'').replace(/"/g,'&quot;').replace(/</g,'&lt;');
const api=(p,m,b)=>fetch(p,{method:m||'GET',headers:{'Authorization':'Bearer '+key.value,'Content-Type':'application/json'},body:b?JSON.stringify(b):undefined}).then(r=>r.json());
async function load(){
  const d=await api('/v1/memories'); const c=d.counts||{};
  out.innerHTML=`<div class=card><span class=stat><b>${c.facts_live||0}</b> 当前事实</span>
   <span class=stat><b>${c.facts_superseded||0}</b> 历史</span>
   <span class=stat><b>${c.episodes||0}</b> 对话</span>
   <span class=stat><b>${c.summaries||0}</b> 摘要</span></div>
   <div class=card><h3>用户画像</h3><pre>${esc(d.profile)||'(空)'}</pre></div>
   <div class=card><h3>事实 · 双时间轴 <span class=dim style="text-transform:none;letter-spacing:0">(✏️改 / 🗑️删,改过即锁定)</span></h3>${(d.facts||[]).map(f=>
     `<div class=f><span class="tg ${f.status=='live'?'live':'old'}">${f.status=='live'?'当前':'历史'}</span>
      <span class=dt>${f.valid_at}</span>
      <span>${esc(f.text)}${f.source=='user'?'<span class=lock>🔒 我设定</span>':''}${f.invalid_at?' <span class=dim>→失效 '+f.invalid_at+'</span>':''}</span>
      <span class=act><button class=ib onclick="editFact('${f.id}','${esc(f.subject)}','${esc(f.predicate)}','${esc(f.object)}')">✏️</button>
      <button class="ib del" onclick="delFact('${f.id}')">🗑️</button></span></div>`).join('')}
      <div class=addf><input id=ns placeholder="主语(默认 user)"><input id=np placeholder="谓语,如 works_at"><input id=no placeholder="宾语,如 字节跳动">
      <button onclick=addFact()>＋ 手动加一条</button></div></div>
   <div class=card><h3>原始对话 + 摘要</h3>${(d.episodes||[]).map(e=>
     `<div class=f><span class=dt>${e.date}</span><span>${esc(e.content)}${e.summary?'<br><span class=dim>摘要: '+esc(e.summary)+'</span>':''}</span></div>`).join('')}</div>`;
}
async function remember(){ if(!msg.value)return; await api('/v1/remember','POST',{content:msg.value}); msg.value=''; load(); }
async function addFact(){ if(!np.value||!no.value)return; await api('/v1/facts','POST',{subject:ns.value||'user',predicate:np.value,object:no.value}); load(); }
async function editFact(id,s,p,o){ const nv=prompt('改成什么(宾语):',o); if(nv==null||nv===o)return; await api('/v1/facts/'+id,'PATCH',{object:nv}); load(); }
async function delFact(id){ if(!confirm('永久删除这条记忆?'))return; await api('/v1/facts/'+id,'DELETE'); load(); }
</script></body></html>"""


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


@app.get("/v1/memories")
def memories(user: str = Depends(auth)):
    """See EVERYTHING stored for this user — the raw episodes, the extracted bi-temporal facts (live and
    superseded, with provenance), and the L2 session summaries. This is the 'look inside my memory' view."""
    from ..util import fmt_date
    mem = mgr().get(user)
    facts = sorted(mem.fact_store.values(), key=lambda f: f.valid_at, reverse=True)
    return {
        "user": user,
        "profile": mem.build_persona(user),
        "counts": {"episodes": len(mem.episodes_doc.values()),
                   "facts_live": sum(1 for f in mem.fact_store.values() if f.is_live()),
                   "facts_superseded": sum(1 for f in mem.fact_store.values() if not f.is_live()),
                   "summaries": len(mem.summary_vec.values())},
        "facts": [{
            "id": f.id,
            "text": f.text, "subject": f.subject, "predicate": f.predicate, "object": f.object,
            "valid_at": fmt_date(f.valid_at),
            "invalid_at": fmt_date(f.invalid_at) if f.invalid_at else None,
            "status": "live" if f.is_live() else "superseded",
            "source": f.source,
            "salience": round(f.salience, 2), "provenance": f.provenance,
        } for f in facts],
        "episodes": [{"date": ep.metadata.get("date") or fmt_date(ep.event_time),
                      "session": ep.session_id, "content": ep.content[:500],
                      "summary": ep.summary} for ep in mem.episodes_doc.values()],
    }


@app.post("/v1/forget")
def forget(user: str = Depends(auth)):
    mgr().forget(user)
    return {"ok": True, "message": f"all memory for '{user}' erased"}


# --- user-authored memory management (the editable layer; user assertions are authoritative) ---
class FactReq(BaseModel):
    subject: str = "user"
    predicate: str
    object: str


class FactEdit(BaseModel):
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None


@app.post("/v1/facts")
def add_fact(req: FactReq, user: str = Depends(auth)):
    """Manually add a fact you assert — it's authoritative and won't be auto-overwritten."""
    m = mgr()
    with m.lock(user):
        mem = m.get(user)
        f = mem.add_fact(req.subject, req.predicate, req.object, user_id=user)
        mem.save()
        return {"ok": True, "id": f.id, "text": f.text}


@app.patch("/v1/facts/{fact_id}")
def edit_fact(fact_id: str, req: FactEdit, user: str = Depends(auth)):
    """Edit a fact's fields; the edit becomes user-authored and sticks."""
    m = mgr()
    with m.lock(user):
        mem = m.get(user)
        f = mem.update_fact(fact_id, subject=req.subject, predicate=req.predicate, object=req.object)
        if f is None:
            raise HTTPException(404, "fact not found")
        mem.save()
        return {"ok": True, "id": f.id, "text": f.text}


@app.delete("/v1/facts/{fact_id}")
def remove_fact(fact_id: str, user: str = Depends(auth)):
    """Right-to-forget: permanently delete a single fact."""
    m = mgr()
    with m.lock(user):
        mem = m.get(user)
        ok = mem.delete_fact(fact_id)
        mem.save()
        return {"ok": ok}
