"""Engram Memory Service — a multi-tenant HTTP API so anyone can connect and manage their own memory
(CLAUDE.md §6, the serving/adoption layer). Each API key is an isolated memory namespace; memory persists
to disk between requests and restarts.

Run it:
    pip install "engram-memory[server]"
    export ENGRAM_API_KEYS="alice:sk-alice-123,bob:sk-bob-456"   # user:key pairs (or ENGRAM_OPEN=1 for dev)
    export ENGRAM_EMBEDDER=hashing          # zero-download default; use bge-small for better local embeddings
    export ENGRAM_LLM=deepseek              # optional: better fact extraction (needs the provider's key)
    uvicorn engram.server.app:app --host 0.0.0.0 --port 8000

Then a client (curl / SDK / the MCP bridge) calls it with `Authorization: Bearer <key>`:
    POST /v1/remember  {"content": "...", "session_id": "..."}   -> store + consolidate
    POST /v1/recall    {"query": "...", "lean": true}            -> a small retrieved context to answer from
    GET  /v1/profile                                             -> the user's synthesized profile
    POST /v1/forget {"confirm": true}                            -> wipe this user's memory

This is the foundation of a hosted product (like Hy-Memory). It is single-node + file-backed by default —
swap the in-memory stores for pgvector/Qdrant + Kuzu (already pluggable behind the store interfaces) and
put it behind your auth/gateway to scale to a public service.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Optional

try:
    from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
    from pydantic import BaseModel, ConfigDict
except Exception as exc:  # noqa: BLE001
    raise SystemExit("the server needs FastAPI — `pip install \"engram-memory[server]\"`") from exc

from ..service import MemoryService


def _load_keys() -> dict[str, str]:
    """Map API key -> user_id from ENGRAM_API_KEYS ('alice:sk-a,bob:sk-b'). Empty => open mode."""
    out: dict[str, str] = {}
    for pair in os.environ.get("ENGRAM_API_KEYS", "").split(","):
        pair = pair.strip()
        if ":" in pair:
            user, key = pair.split(":", 1)
            out[key.strip()] = user.strip()
    return out


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _bearer_token(authorization: str) -> str:
    """Parse a strict Bearer header. Missing auth is distinct from malformed auth so local anonymous
    mode stays possible without accepting accidental or spoofed schemes as namespaces."""
    value = authorization.strip()
    if not value:
        return ""
    scheme, sep, token = value.partition(" ")
    if scheme.lower() != "bearer" or not sep or not token.strip():
        raise HTTPException(401, "expected Authorization: Bearer <token>")
    return token.strip()


_svc: Optional[MemoryService] = None


def svc() -> MemoryService:
    """The shared multi-tenant core (one per process). The MCP server and OpenAI-compatible proxy use
    the same class, so all three surfaces share one implementation."""
    global _svc
    if _svc is None:
        _svc = MemoryService()
    return _svc


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    svc()  # load the embedder (and LLM) once at boot, so the first request doesn't race on it
    yield


app = FastAPI(
    title="Engram Memory Service",
    version="0.1.0",
    description="Multi-tenant long-term memory — connect with a Bearer key and manage your own memory.",
    lifespan=_lifespan,
)


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/v1/"):
        response.headers["Cache-Control"] = "no-store"
    elif path.startswith("/ui/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path == "/ui" or path.startswith("/ui/") or path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


def auth(authorization: str = Header(default="")) -> str:
    """Resolve the caller's user_id from the Bearer key. Dev open mode uses the key text itself as the
    namespace, so anyone can try it without pre-provisioned keys while still avoiding shared anonymous
    memory unless it is explicitly enabled."""
    keys = _load_keys()
    token = _bearer_token(authorization)
    if keys:
        if token not in keys:
            raise HTTPException(401, "invalid or missing API key")
        return keys[token]
    if _env_flag("ENGRAM_OPEN"):
        if token:
            return token
        if _env_flag("ENGRAM_ALLOW_ANONYMOUS"):
            return "anonymous"
        raise HTTPException(
            401,
            "missing bearer namespace: in ENGRAM_OPEN mode send Authorization: Bearer <namespace>, "
            "or set ENGRAM_ALLOW_ANONYMOUS=1 to allow shared anonymous memory",
        )
    raise HTTPException(401, "set ENGRAM_API_KEYS (user:key,...) or ENGRAM_OPEN=1")


class RememberReq(BaseModel):
    content: str
    session_id: str = "default"
    scope: str = "auto"  # auto (route by ephemerality) | long (force long-term) | working (force ephemeral)


class RecallReq(BaseModel):
    query: str
    lean: bool = True
    n_chunks: int = 6
    session_id: Optional[str] = None  # when set, recall also surfaces this session's working memory
    as_of: Optional[float] = None  # epoch seconds: point-in-time memory view
    redact_sensitive: bool = False  # hide facts tagged sensitive from the returned context/fact list


class CloseSessionReq(BaseModel):
    session_id: str = "default"
    summarize: bool = True
    clear_working: bool = True


@app.get("/health")
def health():
    service = svc()
    keys = _load_keys()
    auth_mode = "api_keys" if keys else ("open" if _env_flag("ENGRAM_OPEN") else "disabled")
    return {
        "ok": True,
        "ready": True,
        "service": "engram",
        "auth_mode": auth_mode,
        "anonymous_allowed": auth_mode == "open" and _env_flag("ENGRAM_ALLOW_ANONYMOUS"),
        "embedder": service.embedder.__class__.__name__,
        "llm_configured": service.llm is not None,
        "answerer_configured": service.answerer is not None,
        "storage": service.config.storage,
        "users_hot": service.hot_count,
        "max_hot_users": service.max_hot_users,
        "max_hot_facts": service.config.max_hot_facts,
    }


# The production console (the React app in frontend/) is served at /ui once built; "/" redirects
# there. When it ISN'T built (fresh clone, tests, the zero-setup demo) we fall back to the tiny inline
# dashboard below — so the server is always usable with no build step (CLAUDE.md zero-setup invariant).
_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "dist")


def _spa_built() -> bool:
    return os.path.isfile(os.path.join(_DIST, "index.html"))


@app.get("/")
def root():
    from fastapi.responses import HTMLResponse, RedirectResponse
    if _spa_built():
        return RedirectResponse(url="/ui/")
    return HTMLResponse(_VIEWER_HTML)


@app.get("/robots.txt")
def robots_txt():
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(
        "User-agent: *\n"
        "Disallow: /ui\n"
        "Disallow: /ui/\n"
        "Disallow: /v1/\n"
        "Disallow: /health\n"
    )


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
.tabs{display:flex;gap:6px;margin:18px 0 4px;flex-wrap:wrap}
.tab{padding:8px 16px;font-size:13.5px;border-radius:10px;background:rgba(255,255,255,.05);font-weight:600;border:1px solid rgba(255,255,255,.1)}
.tab.on{background:linear-gradient(90deg,#22d3ee,#a78bfa);color:#04121a;border:none}
.tl{position:relative}.tl:before{content:"";position:absolute;left:90px;top:6px;bottom:6px;width:2px;background:rgba(255,255,255,.1)}
.tlrow{display:flex;gap:12px;align-items:baseline;padding:8px 0;position:relative}
.tldate{min-width:74px;color:#22d3ee;font-size:11.5px;text-align:right;flex:none}
.tldot{width:9px;height:9px;border-radius:50%;background:#34d399;flex:none;align-self:center;z-index:1;box-shadow:0 0 0 3px #070b14}
.tlrow.superseded .tldot{background:#475569}.tlrow.superseded .tltext{color:#8a97b8;text-decoration:line-through}
.tltext{font-size:14.5px}
.upd{font-size:10px;color:#c4b5fd;border:1px solid rgba(167,139,250,.4);border-radius:6px;padding:1px 6px;margin-left:6px;text-decoration:none}
.old2{font-size:10px;color:#8a97b8;margin-left:6px;text-decoration:none}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.tag{background:rgba(34,211,238,.14);color:#67e8f9;border-radius:8px;padding:4px 10px;font-size:13px;display:inline-flex;gap:7px;align-items:center}
.tag.mute{background:rgba(251,113,133,.14);color:#fda4af}
.tag b{cursor:pointer;opacity:.6;font-weight:400}.tag b:hover{opacity:1}
.pbtn{margin:6px 10px 6px 0;padding:10px 16px}
svg text{font-family:system-ui,sans-serif}
</style></head><body>
<h1>🧠 我的记忆 <span class=dim>Engram</span></h1>
<p class=dim>输入你的 API key,看 / 编辑 / 删除你自己的记忆。你手动改的会被锁定 🔒,不会被自动覆盖。</p>
<div class=bar><input id=key placeholder="API key(开放模式下随便填,比如 1)" value="1">
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
    # Route by ephemerality inside the service: either way the dated episode is stored (history stays
    # answerable); transient state also goes to working memory and is NOT promoted into a durable fact.
    return svc().remember(user, req.content, session_id=req.session_id, scope=req.scope)


@app.post("/v1/recall")
def recall(req: RecallReq, user: str = Depends(auth)):
    # answer=True: also generate a real answer over the lean context + report the full-context baseline
    # token count, so the console's 问答 view can show the answer AND the token saving.
    return svc().recall(user, req.query, lean=req.lean, n_chunks=req.n_chunks,
                        session_id=req.session_id, as_of=req.as_of,
                        redact_sensitive=req.redact_sensitive, answer=True)


@app.post("/v1/sessions/close")
def close_session(req: CloseSessionReq, user: str = Depends(auth)):
    """End a client session: drain pending consolidation, index summaries, clear ephemeral working state.

    Raw episodes remain stored for provenance and future as-of reads; this is a lifecycle/grooming hook
    for agent clients, not a transcript deletion endpoint.
    """
    return svc().close_session(
        user,
        req.session_id,
        summarize=req.summarize,
        clear_working=req.clear_working,
    )


@app.get("/v1/sessions")
def sessions(
    limit: Optional[int] = None,
    offset: int = 0,
    q: str = "",
    user: str = Depends(auth),
):
    """Content-free session index: session ids, counts, timestamps, and backlog state.

    Use this to show a user which Codex/Claude/app sessions touched their memory before drilling into a
    specific `/v1/sessions/report`.
    """
    return svc().sessions(user, limit=limit, offset=offset, q=q)


@app.get("/v1/sessions/report")
def session_report(
    session_id: str,
    include_sensitive: bool = False,
    user: str = Depends(auth),
):
    """Audit what a specific session contributed to durable memory.

    Sensitive fact text is redacted by default. Pass include_sensitive=true only for explicit user-owned
    audits where showing private fact text is intended.
    """
    return svc().session_report(user, session_id, include_sensitive=include_sensitive)


@app.get("/v1/profile")
def profile(user: str = Depends(auth)):
    return svc().profile(user)


@app.get("/v1/profile/structured")
def structured_profile(user: str = Depends(auth)):
    """L2 structured profile: basic info + preferences (by category) + habits, split into confirmed vs
    待确认 (tentative). Display-only tiering — does NOT affect what recall/search can see."""
    return svc().structured_profile(user)


@app.get("/v1/memories")
def memories(
    facts_limit: Optional[int] = None,
    facts_offset: int = 0,
    episodes_limit: Optional[int] = None,
    episodes_offset: int = 0,
    status: Optional[str] = None,
    q: str = "",
    include_sensitive: bool = False,
    user: str = Depends(auth),
):
    """Browse stored memory.

    Defaults to a share-safe view: non-sensitive facts plus content-free counts, with profile/raw episodes
    omitted. Pass `include_sensitive=true` only for an explicit owner-visible inspection view with raw
    episodes, summaries, profile, and sensitive facts.
    """
    return svc().memories(
        user,
        facts_limit=facts_limit,
        facts_offset=facts_offset,
        episodes_limit=episodes_limit,
        episodes_offset=episodes_offset,
        status=status,
        q=q,
        include_sensitive=include_sensitive,
    )


@app.get("/v1/stats")
def stats(user: str = Depends(auth)):
    """Content-free namespace stats for dashboards and readiness checks. Unlike /v1/memories, this does
    not return profile text, fact text, episode snippets, or storage paths."""
    return svc().stats(user)


@app.get("/v1/agent/status")
def agent_status(session_id: Optional[str] = None, user: str = Depends(auth)):
    """Content-free status for agent clients before they decide whether to recall/remember/close.

    This returns namespace/session counts, focus policy, and recommended tool calls without exposing
    fact text, profile prose, raw episodes, summaries, or storage paths.
    """
    return svc().agent_status(user, session_id=session_id)


class ForgetReq(BaseModel):
    confirm: bool = False


@app.post("/v1/forget")
def forget(req: Optional[ForgetReq] = None, confirm: bool = False, user: str = Depends(auth)):
    if user in {"anonymous", "1"}:
        raise HTTPException(403, "the public demo/anonymous namespace cannot be wiped")
    if not confirm and not (req and req.confirm):
        raise HTTPException(400, "forget requires explicit confirm=true because it permanently erases this namespace")
    return svc().forget(user)


# --- batch import: bring your own history (ChatGPT export / OpenAI messages / JSONL / transcript) ---
class ImportReq(BaseModel):
    # Either pass already-parsed `sessions` (from engram.connectors.parse / the import CLI), OR raw
    # `data` + a `format` to parse server-side. `sessions` wins when both are given.
    sessions: Optional[list] = None
    data: Optional[object] = None
    format: str = "auto"
    consolidate: bool = True
    summarize: bool = True


@app.post("/v1/import")
def import_history(req: ImportReq, user: str = Depends(auth)):
    """Bulk-ingest an external history in one batched pass. See `python -m engram.connectors` for a CLI
    that parses common exports and posts here."""
    if req.sessions is None and req.data is None:
        raise HTTPException(400, "provide either 'sessions' (pre-parsed) or 'data' (+ 'format') to import")
    return svc().import_(user, sessions=req.sessions, data=req.data, format=req.format,
                         consolidate=req.consolidate, summarize=req.summarize)


# --- OpenAI-compatible chat with transparent memory (drop-in: point your OpenAI client's base_url here) --
class ChatCompletionReq(BaseModel):
    model_config = ConfigDict(extra="allow")  # accept (and ignore) temperature/top_p/etc.
    model: str = "engram"
    messages: list[dict] = []
    stream: bool = False
    # Engram extension: recall/remember/session_id/scope/n_chunks/as_of/redact_sensitive.
    memory: Optional[dict] = None


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionReq, background: BackgroundTasks, user: str = Depends(auth)):
    """OpenAI-compatible chat completions, augmented with long-term memory: recall a relevant slice for
    the latest user turn, inject it, generate with the configured LLM, then remember the turn off the
    critical path. Set body.memory={"recall":false}, {"remember":false}, {"session_id":"..."},
    {"scope":"working"}, {"as_of":...}, or {"redact_sensitive":true} to control the memory layer."""
    from . import openai_compat as oc

    if not req.messages:
        raise HTTPException(400, "messages must be a non-empty list")
    opts = req.memory or {}
    session_id = str(opts.get("session_id") or "default")
    scope = str(opts.get("scope") or "auto")
    body = req.model_dump()
    try:
        resp = oc.chat_completion(
            svc(),
            user,
            body,
            n_chunks=int(opts.get("n_chunks", 6)),
            do_recall=opts.get("recall", True),
            as_of=opts.get("as_of"),
            redact_sensitive=bool(opts.get("redact_sensitive", False)),
            session_id=session_id,
        )
    except oc.NoLLMConfigured as exc:
        raise HTTPException(503, str(exc))

    user_text = oc.latest_user_text(body.get("messages", []))
    if opts.get("remember", True) and user_text:
        background.add_task(
            svc().remember,
            user,
            user_text,
            session_id=session_id,
            scope=scope,
        )  # write off the response path (System-2)
        resp["engram"]["remembered"] = True
        resp["engram"]["remember_scope"] = scope

    if req.stream:
        from fastapi.responses import StreamingResponse
        return StreamingResponse(oc.iter_sse(resp), media_type="text/event-stream")
    return resp


# --- user-authored memory management (the editable layer; user assertions are authoritative) ---
class FactReq(BaseModel):
    subject: str = "user"
    predicate: str
    object: str
    sensitive: Optional[bool] = None
    category: Optional[str] = None


class FactEdit(BaseModel):
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object: Optional[str] = None
    sensitive: Optional[bool] = None  # user override of the auto sensitivity flag (⑤)
    category: Optional[str] = None


@app.post("/v1/facts")
def add_fact(req: FactReq, user: str = Depends(auth)):
    """Manually add a fact you assert — it's authoritative and won't be auto-overwritten."""
    return svc().add_fact(
        user,
        req.subject,
        req.predicate,
        req.object,
        sensitive=req.sensitive,
        category=req.category,
    )


@app.patch("/v1/facts/{fact_id}")
def edit_fact(fact_id: str, req: FactEdit, user: str = Depends(auth)):
    """Edit a fact's fields; the edit becomes user-authored and sticks."""
    result = svc().update_fact(user, fact_id, subject=req.subject, predicate=req.predicate,
                               object=req.object, sensitive=req.sensitive, category=req.category)
    if result is None:
        raise HTTPException(404, "fact not found")
    return result


@app.delete("/v1/facts/{fact_id}")
def remove_fact(fact_id: str, user: str = Depends(auth)):
    """Right-to-forget: permanently delete a single fact."""
    return svc().delete_fact(user, fact_id)


# --- ③ focus areas: customize what memory emphasizes (track) or suppresses (mute) ---
class FocusReq(BaseModel):
    track: Optional[list[str]] = None  # None = leave unchanged; [] = clear
    mute: Optional[list[str]] = None


@app.get("/v1/focus")
def get_focus(user: str = Depends(auth)):
    return svc().get_focus(user)


@app.put("/v1/focus")
def put_focus(req: FocusReq, user: str = Depends(auth)):
    """Set the user's tracked / muted topics. Tracked topics gain salience (rank higher, stay hot);
    muted topics are hidden from recall + profile."""
    return svc().set_focus(user, track=req.track, mute=req.mute)


# --- memory policy: editable prompts + "what to record" directive (the 记忆策略 page) ---
class PolicyReq(BaseModel):
    extract_instruction: Optional[str] = None  # None = leave unchanged; "" = reset to default
    extract_system: Optional[str] = None
    summary_system: Optional[str] = None
    persona_system: Optional[str] = None


@app.get("/v1/policy")
def get_policy(user: str = Depends(auth)):
    """The user's prompt overrides AND the built-in defaults (so the console can show/edit either)."""
    return svc().get_policy(user)


@app.put("/v1/policy")
def put_policy(req: PolicyReq, user: str = Depends(auth)):
    """Set the editable extraction/summary/persona prompts and the 'what to record' directive. Takes
    effect on the next remember()/consolidation."""
    return svc().set_policy(user, **req.model_dump())


# --- ① working memory: ephemeral, session/TTL-scoped state kept out of long-term ---
class WorkingReq(BaseModel):
    content: str
    session_id: str = "default"
    kind: str = "state"  # state | intent | schedule | note | ...
    ttl_seconds: Optional[float] = None  # hard expiry; None => lives until the session is cleared


@app.post("/v1/working")
def add_working(req: WorkingReq, user: str = Depends(auth)):
    """Store an ephemeral item (won't be consolidated into long-term or the profile)."""
    return svc().add_working(user, req.content, session_id=req.session_id, kind=req.kind,
                             ttl_seconds=req.ttl_seconds)


@app.get("/v1/working")
def list_working(session_id: Optional[str] = None, user: str = Depends(auth)):
    """Live working-memory items (optionally scoped to a session). Expired/consumed are excluded."""
    return svc().working_memory(user, session_id=session_id)


@app.delete("/v1/working")
def clear_working(session_id: str, user: str = Depends(auth)):
    """End-of-session / power-cycle clear: drop this session's working memory."""
    return svc().clear_working(user, session_id)


# --- suspected conflicts (LLM-detected in System-2, user-confirmed; never auto-resolved) ---
class ResolveReq(BaseModel):
    keep: str = "newer"  # newer | older | both(=dismiss)


@app.get("/v1/conflicts")
def conflicts(user: str = Depends(auth)):
    """Suspected conflicts awaiting the user's decision (never auto-resolved). Needs the System-2 LLM
    detector enabled (ENGRAM_CONFLICT_DETECTION=1)."""
    return svc().conflicts(user)


@app.post("/v1/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: str, req: ResolveReq, user: str = Depends(auth)):
    """Apply the user's decision: keep newer/older (supersede the other) or both (dismiss)."""
    return svc().resolve_conflict(user, conflict_id, keep=req.keep)


# --- ② semantic graph for the 关系图谱 visualization ---
@app.get("/v1/graph")
def graph(
    as_of: float | None = None,
    include_sensitive: bool = False,
    q: str = "",
    live_only: bool = False,
    limit: Optional[int] = None,
    user: str = Depends(auth),
):
    """Nodes (entities) + edges (bi-temporal relations) of this user's semantic graph.
    Defaults to a share-safe graph that omits edges backed by facts tagged sensitive. Pass
    `include_sensitive=true` only for an explicit owner-visible graph inspection."""
    return svc().graph(
        user,
        as_of=as_of,
        include_sensitive=include_sensitive,
        q=q,
        live_only=live_only,
        limit=limit,
    )


# --- ④ privacy: full data export (GDPR-style portability); erase is POST /v1/forget ---
@app.get("/v1/export")
def export(include_sensitive: bool = False, user: str = Depends(auth)):
    """Download this user's memory as JSON (data portability).

    Defaults to a share-safe structured export: non-sensitive facts + their graph, with free-text
    profile/episodes/summaries omitted. Pass `include_sensitive=true` only for an explicit full private
    export with every fact's bi-temporal stamps + provenance, raw episodes, summaries, profile, and focus.
    """
    from fastapi.responses import JSONResponse

    data = svc().export(user, include_sensitive=include_sensitive)
    fname = f"engram_{''.join(c for c in user if c.isalnum()) or 'me'}_export.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# --- serve the production console (frontend/dist) as a single-page app under /ui ---
# Hashed assets come from /ui/assets via StaticFiles; every other /ui path returns index.html so
# client-side routes (e.g. /ui/facts) survive a hard refresh. Only wired when the SPA is built.
if _spa_built():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/ui/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")

    @app.head("/ui")
    @app.head("/ui/{path:path}")
    @app.get("/ui")
    @app.get("/ui/{path:path}")
    def spa(path: str = ""):
        candidate = os.path.join(_DIST, path)
        if path and os.path.isfile(candidate):  # favicon, etc.
            return FileResponse(candidate)
        return FileResponse(os.path.join(_DIST, "index.html"))
