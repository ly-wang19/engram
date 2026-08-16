# Engram 记忆服务 · 接口文档

一套多租户的长期记忆 HTTP API：生产模式下，**API key 映射到稳定租户命名空间**，同一租户可配置多个 key
做无停机轮换；不同租户互相隔离。显式开发开放模式才把 Bearer 文本本身当作命名空间。

---

## 0. 两种用法（任选）

**A. 直接用已部署的服务（最快，零搭建）**
- Base URL：`http://42.193.220.197:8456`
- 自己起一个 key 当命名空间（例如 `demo-test`），数据互相隔离。
- 只适合公开演示；数据会存在该服务器上，请勿发送隐私或生产数据。

**B. 自己部署（数据完全本地）**
```bash
cp deploy/.env.example deploy/.env
# 把 deploy/.env 的 ENGRAM_API_KEYS 示例值换成 tenant:强随机密钥
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
curl -fsS http://127.0.0.1:8000/ready
```
> 生产不要设置 `ENGRAM_OPEN`/`ENGRAM_ALLOW_ANONYMOUS`。直接 Python 部署可安装
> `engram-memory[server]`，设置 `ENGRAM_API_KEYS="alice:key-a,bob:key-b"` 后运行 Uvicorn。
> 同一租户轮换：`alice:key-new,alice:key-old`；一个 key 不允许映射到多个租户。

跨 Claude Code / Codex / Cursor / 自研 agent 的推荐生命周期见
[`docs/cross-agent-memory.md`](docs/cross-agent-memory.md) 和
[`docs/agent-adapters.md`](docs/agent-adapters.md)：开工先看 agent status，回答前 recall，长期事实 remember，
线程结束 close session，再用 session report 审计这一轮保存了什么。单会话生命周期自测（本地零服务或 HTTP）见
[`examples/cross_agent_lifecycle.py`](examples/cross_agent_lifecycle.py)。

---

## 1. 鉴权
所有 `/v1/*` 接口都要带：
```
Authorization: Bearer <你的key>
Content-Type: application/json
```

密钥有两种来源，解析顺序为「运行时签发的密钥 → `ENGRAM_API_KEYS` 静态映射 → `ENGRAM_OPEN` 开放模式」。
运行时密钥优先，所以吊销不会被残留的环境变量条目复活。

### 1.1 运行时签发密钥（不用重启）

静态映射要改环境变量再重启。托管部署可以用管理端点在运行中签发和吊销。**管理面默认不存在**——
不设 `ENGRAM_ADMIN_TOKEN` 时这几个端点一律 403，所以开放模式部署不会被路人签发租户。

```bash
export ENGRAM_ADMIN_TOKEN=<强随机串>   # 与租户 key 不同，是独立的管理凭据

# 签发（明文密钥只在这一次响应里出现，服务端只存 SHA-256 摘要，丢了只能重签不能找回）
curl -s -X POST $B/v1/admin/keys -H "Authorization: Bearer $ENGRAM_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"user":"alice","label":"laptop"}'
# -> {"id":"key_...","user":"alice","label":"laptop","created_at":...,"revoked":false,"key":"sk-engram-..."}

curl -s $B/v1/admin/keys -H "Authorization: Bearer $ENGRAM_ADMIN_TOKEN"          # 列出（不含密钥与摘要）
curl -s -X DELETE $B/v1/admin/keys/key_xxx -H "Authorization: Bearer $ENGRAM_ADMIN_TOKEN"  # 立即吊销
```

密钥文件（数据目录下的 `api_keys.json`）仅属主可读写，且只保存摘要。若该文件损坏，服务**拒绝启动
密钥库并对请求返回 503**，而不是以空状态继续——后者会先让所有已签发密钥失效，再在下次签发时覆盖掉
那些只是读不出来的记录。

### 1.2 重试安全（Idempotency-Key）

`/v1/remember` 与 `/v1/import` 支持 `Idempotency-Key` 请求头。客户端超时重试时，服务端按
「租户 + key」返回首次的响应而不重跑：首次请求其实成功了，丢的只是响应。

```bash
curl -s -X POST $B/v1/remember -H "Authorization: Bearer $KEY" \
  -H 'Idempotency-Key: 2026-08-16-abc' -H 'Content-Type: application/json' \
  -d '{"content":"..."}'
```

缓存是进程内的，默认保留 `ENGRAM_IDEMPOTENCY_TTL_S`（86400）秒。多副本部署下，重试若路由到另一个
副本仍会重跑——需要跨副本幂等就换成共享存储。

### 1.3 限流

`ENGRAM_RATE_LIMIT_PER_MIN`（默认 0 = 关闭）按租户做滑动窗口限流，超限返回 `429` 并带
`Retry-After`。`/health`、`/ready`、`/metrics` 不鉴权也不限流——租户被限流时探针必须还能用。
同样是进程内的：多副本下有效限额是 `每分钟配额 × 副本数`。

健康检查不需要鉴权：
```bash
curl -s $B/health
curl -f $B/ready
```
`/health` 是 liveness/诊断，始终返回不含密钥/用户内容的运行形态；`/ready` 只有在鉴权和存储可接流量时
返回 200，否则返回 503：
```json
{
  "ok": true,
  "ready": true,
  "service": "engram",
  "version": "0.1.0",
  "auth_mode": "api_keys",
  "anonymous_allowed": false,
  "embedder": "HashingEmbedder",
  "llm_configured": false,
  "answerer_configured": false,
  "storage": "memory",
  "users_hot": 0,
  "max_hot_users": 64
}
```

## 2. 写入记忆

### 2.1 灌对话（自动抽取事实）— 主用
`POST /v1/remember`
```json
{ "content": "用户：我在字节跳动做后端，最喜欢周杰伦。\n助手：好的，记住啦。",
  "session_id": "s1",          // 同一会话用同一个 id（可选）
  "scope": "auto" }            // auto(默认) | long(强制长期) | working(临时,不进长期)
```
返回：`{"ok":true,"extracted":2,"total_facts":N}`，或临时记忆 `{"ok":true,"scope":"working",...}`。
> `scope=auto` 会把"今天嗓子不舒服"这类临时状态自动路由到工作记忆（不污染长期画像，但原始对话仍按日期留存、可查询）。批量灌长期记忆建议 `scope:"long"`。

### 2.2 直接写结构化事实（已抽取好的）
`POST /v1/facts`
```json
{ "subject": "user", "predicate": "works_at", "object": "字节跳动" }
```
> 手动写/改的事实标记为"用户设定 🔒"，权威，不会被自动抽取覆盖。

## 3. 检索 / 测效果

### 3.1 召回 + 答题 + 省 token 对比 — 主用
`POST /v1/recall`
```json
{ "query": "我最喜欢哪个歌手", "lean": true, "n_chunks": 4, "as_of": null, "redact_sensitive": false }
```
`as_of` 可选，传 epoch 秒时返回该历史时刻的记忆视图。
`redact_sensitive=true` 时只返回已分类的非敏感结构化事实，不包含画像、摘要或原始对话片段，适合
共享上下文或注入第三方模型。
返回：
```json
{ "answer": "你最喜欢周杰伦。",
  "context": "USER PROFILE:...\nFACTS:...\nRELEVANT CONVERSATIONS:...",  // 喂给模型的精炼上下文
  "tokens_est": 9958,        // 精炼上下文 token
  "full_tokens": 81408 }     // 整段历史 token（full-context 基线）→ 省 8×
```

### 3.2 结束会话整理 — agent 客户端收尾
`POST /v1/sessions/close`
```json
{ "session_id": "codex:super-memory:thread-123",
  "summarize": true,
  "clear_working": true }
```
用于 Claude Code / Codex / Cursor 这类客户端在一个线程结束或切换任务时调用。它不会删除原始对话，
而是补齐这个 session 的待整理 episode、生成缺失摘要、把知识更新反映到摘要层、清掉该 session 的
临时 working memory，并保存到磁盘。

如需审计这一轮到底写入了哪些长期事实：
`GET /v1/sessions/report?session_id=codex:super-memory:thread-123`

默认 `include_sensitive=false`，敏感事实正文会被替换为 `[redacted sensitive fact]`；用户明确要完整私有审计时再传
`include_sensitive=true`。

推荐生命周期：
```text
会话开始 / 接线自检：GET /v1/agent/status?session_id=...
回答前：POST /v1/recall
用户说了稳定偏好、项目约定、长期事实：POST /v1/remember
会话结束 / 切换任务：POST /v1/sessions/close
需要审计保存结果：GET /v1/sessions/report?session_id=...
```

### 3.3 Agent 接线状态 — 不返回记忆正文
`GET /v1/agent/status?session_id=codex:super-memory:thread-123`

返回当前 key/namespace、session 计数、working memory 数、focus 策略、长期记忆计数和建议下一步调用。
它不返回 profile、fact 文本、原始对话或摘要，适合 agent 开工前确认自己接到了正确的记忆层。

### 3.4 OpenAI-compatible chat 的 memory 扩展
`POST /v1/chat/completions` 可在请求体里带：
```json
{ "model": "engram",
  "messages": [{ "role": "user", "content": "继续上次 benchmark 的事" }],
  "memory": {
    "session_id": "codex:super-memory:thread-123",
    "recall": true,
    "remember": true,
    "scope": "auto",
    "n_chunks": 6,
    "as_of": null,
    "redact_sensitive": false
  } }
```
这样已有 OpenAI SDK 应用只要换 `base_url`，就能在回答前自动召回、回答后把用户轮次写回同一个
agent session。

## 4. 查看 / 管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/agent/status?session_id=...` | Agent 开工自检：namespace/session/focus/counts/下一步建议；不含记忆正文 |
| GET | `/v1/sessions?limit=20&q=codex` | 跨 agent/app 会话索引：session id、时间、episode/fact/working 计数；不含正文 |
| GET | `/v1/sessions/report?session_id=...` | 审计某个 session 写入了哪些长期事实；默认隐藏敏感事实正文 |
| GET | `/v1/stats` | 内容无关统计：episode pending/consolidated、hot/cold facts、cold page-in/out、时间范围、敏感事实数量、pending conflicts、运行后端；适合监控 |
| GET | `/v1/memories` | 默认安全视图：非敏感 facts + 数量；完整私有查看需 `include_sensitive=true` |
| GET | `/v1/profile/structured` | 结构化用户画像(基本信息/偏好/习惯) |
| GET | `/v1/graph` | 默认安全关系图：排除敏感 fact 对应的边；完整私有图需 `include_sensitive=true` |
| GET | `/v1/conflicts` | 待确认的疑似冲突(LLM 检测，需开 `ENGRAM_CONFLICT_DETECTION=1`) |
| POST | `/v1/conflicts/{id}/resolve` | `{"keep":"newer\|older\|both"}` 让冲突由人确认 |
| PATCH | `/v1/facts/{id}` | 改事实 `{"object":"...","sensitive":true}` |
| DELETE | `/v1/facts/{id}` | 删一条 |
| GET | `/v1/export?include_sensitive=false` | 安全结构化导出：仅非敏感 facts + graph；`include_sensitive=true` 为完整可迁移导出（双时间戳、supersedes 链、provenance、原始对话、摘要、focus） |
| POST | `/v1/import` | 批量导入：`{"data":..., "format":"chatgpt\|messages\|records\|jsonl\|transcript\|engram\|auto"}`；`engram` 格式（自动嗅探）直接还原一份 `/v1/export` 导出——跨实例迁移路径 |
| POST | `/v1/forget` | 需 `{"confirm":true}`；清空该 key 的全部记忆（不可逆） |

### 运行指标（`GET /metrics`，不鉴权）

与 `/health` 一样开放，因为载荷**按构造只含聚合量**：没有命名空间名、没有查询、没有正文，所以它
无法暴露某个租户是否存在。用于回答「写路径 <50ms、读路径 <100ms 这两个目标现在还成立吗」——
没有实时分位数的话，那两个数字只是断言而非测量。

```bash
curl -s $B/metrics
```

```json
{
  "uptime_s": 1234.5,
  "ops": {"remember": {"n": 42, "p50_ms": 8.1, "p95_ms": 22.4, "avg_ms": 9.7, "max_ms": 31.0, "window": 42}},
  "counts": {"rate_limited": 3, "idempotent_replays": 1, "auth_rejected": 0, "remember_degraded": 0},
  "tokens": {"context_total": 9600, "calls_with_baseline": 12, "savings_ratio": 8.2}
}
```

- `ops` 是每个操作的滑动窗口分位数，反映**当前**表现，不会被历史均值稀释。
- `counts` 里 `rate_limited` / `idempotent_replays` / `auth_rejected` 让三层防护可见——否则无法判断
  它们是否在生效。`auth_misconfigured`（503）与 `auth_rejected`（401）分开计：前者是配置坏了，
  后者是调用方拿错了密钥。
- `savings_ratio` **只用两侧都测量过的调用**计算（即带 `answer=true` 的召回），没有配对样本时为
  `null` 而不是编一个数。

### 跨实例迁移（换服务器 / 换账号）

记忆属于用户，不属于某一个部署。把一个 namespace 从实例 A 搬到实例 B：

```bash
# 1. 从 A 完整导出（含敏感事实与原始对话）
curl -s "$A/v1/export?include_sensitive=true" -H "Authorization: Bearer $KEY_A" > export.json
# 2. 导入 B（事实保留原 id/双时间戳/supersedes 链；目标端用自己的 embedder 重新向量化）
curl -s -X POST "$B/v1/import" -H "Authorization: Bearer $KEY_B" -H "Content-Type: application/json" \
  -d "{\"data\": $(cat export.json), \"format\": \"engram\"}"
```

导入按 id 幂等：已存在的条目跳过、绝不覆盖，重复导入不会产生重复记忆。两端 embedder 可以不同——
这也是更换 embedder 的官方迁移路径。CLI 等价：`python -m engram.connectors -f export.json --api-url $B --key $KEY_B`。

## 5. 控制台（可视化）
浏览器开 **`<Base URL>/ui/`** → 输入你的 key → 看「画像 / 事实管理 / 时间线 / 关系图谱 / 记忆问答 / 冲突待确认」。

---

## 6. 批量灌入（脚本）
把数据整理成一个 JSON 文件（一个数组，每条是对话或事实），用配套脚本一键灌：
```bash
python examples/ingest_client.py \
  --base http://42.193.220.197:8456 --key demo-test \
  --data your_data.json --reset
```
数据格式见 `examples/ingest_client.py` 顶部说明（支持"原始对话"和"结构化事实"两种记录）。

---

## 7. 最小可跑示例（curl）
```bash
B=http://42.193.220.197:8456 ; K=demo-test
# 灌
curl -s -X POST $B/v1/remember -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"content":"我在字节跳动做后端，最喜欢周杰伦。","scope":"long"}'
# 测
curl -s -X POST $B/v1/recall -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"query":"我喜欢哪个歌手"}'
```
