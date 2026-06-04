# Engram 记忆控制台 (Memory Console)

A production-grade web UI for managing your Engram long-term memory — view, ask, edit,
customize, and export everything the engine remembers about you.

> 这是给"小白"也能看懂、也能自己管理记忆的前端：查看用户画像、增删改记忆、定制关注点、
> 可视化时间线与关系图谱、一键导出或清空。对标腾讯 Hunyuan 的记忆管理体验，但**完全开源**。

## Stack

| Concern | Choice |
|---|---|
| Framework | React 18 + TypeScript |
| Build | Vite 5 |
| Styling | Tailwind CSS 3 (brand design tokens in `tailwind.config.ts`) |
| Routing | React Router 6 |
| Server state | TanStack Query 5 |
| Auth/UI state | Zustand (persisted API key) |
| Icons | lucide-react |

No heavy graph/chart dependency: the knowledge-graph view (`src/components/ForceGraph.tsx`)
uses a small dependency-free force-directed layout.

## Internationalization (中文 / English)

The whole UI ships in **both Chinese and English**, switchable live from the 中/EN toggle in the
top bar (and on the login screen). No i18n library — it's a tiny type-safe layer on the same
Zustand pattern the rest of the app uses:

- `src/i18n/en.ts` is the canonical dictionary; `src/i18n/zh.ts` is typed `Dict` (= `typeof en`),
  so a missing or extra key in either language is a **compile error** — the two stay in lockstep.
- `useT()` returns the active dictionary and re-renders on change; `getT()` reads it outside React
  (e.g. the fetch client's error messages in `src/lib/api.ts`).
- The choice is persisted (`engram.lang` in localStorage). First visit defaults to the browser
  language (`zh*` → Chinese, else English); `src/App.tsx` keeps `<html lang>` and the tab title in sync.

To add a string: add the key to `en.ts`, then `zh.ts` (the compiler tells you if you forget), and
use it via `const t = useT()` → `t.<section>.<key>`. Values can be functions for interpolation.

## Views

- **总览 Dashboard** — stats, L3 persona, quick "remember".
- **记忆问答 Ask** — retrieve a *lean* context and see how few tokens answer a question (Bet A/E).
- **事实管理 Facts** — full bi-temporal CRUD; user edits are authoritative (🔒, never auto-overwritten).
- **时间线 Timeline** — chronological evolution with supersession (live vs. replaced).
- **关系图谱 Graph** — entities + relations; solid = current, dashed = invalidated.
- **关注点 Focus** — track (salience boost) / mute (hide) topics. Real wiring, not cosmetic.
- **原始对话 Conversations** — raw episodes + L2 summaries.
- **隐私与数据 Privacy** — full JSON export (data portability) + erase (right to be forgotten).

## Develop

```bash
pnpm install
# run the memory server in another terminal (serves the API on :8000)
#   ENGRAM_OPEN=1 ENGRAM_EMBEDDER=bge-small uvicorn engram.server.app:app --port 8000
pnpm dev            # Vite dev server on http://localhost:5173/ui/ (proxies /v1 + /health to :8000)
```

## Build (served by the Python server)

```bash
pnpm build          # → frontend/dist
```

The FastAPI memory server (`engram/server/app.py`) auto-detects `frontend/dist` and serves the
console at **`/ui`** (with SPA fallback so deep links survive a refresh); `/` redirects there.
When `dist/` isn't built, the server falls back to a tiny inline dashboard, so the API is always
usable with no Node build step (the project's zero-setup invariant).

Configure a non-same-origin API with `VITE_API_BASE` (see `.env.example`).
