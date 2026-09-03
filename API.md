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

配置了 LLM 时，关闭会话还会把这段会话蒸馏成结论事实（`decision` / `finding` / `lesson` /
`open_question`，谓词即结论类型、主语是 session id），返回体里的 `outcomes` 是本次新增的结论条数；
重复关闭同一个未变化的 session 不会重复写入。这是**每次关会话花一次 LLM 调用**的默认行为：
请求体可传 `"outcomes": false` 单次关闭，运维要设成本上限则设 `ENGRAM_SESSION_OUTCOMES=0`
（服务端强制关闭，请求体的 `outcomes: true` 也无法解除）。没有配置 LLM 时该分支不执行，`outcomes` 恒为 0。

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
| GET | `/v1/memories` | 默认安全视图：非敏感 facts + 数量；完整私有查看需 `include_sensitive=true`；`kind=outcomes` 只看会话结论、`kind=attributes` 只看属性事实（未知值忽略，与 `status` 同语义） |
| GET | `/v1/profile/structured` | 结构化用户画像(基本信息/偏好/习惯) |
| GET | `/v1/graph` | 默认安全关系图：排除敏感 fact 对应的边；完整私有图需 `include_sensitive=true` |
| GET | `/v1/conflicts` | 待确认的疑似冲突(LLM 检测，需开 `ENGRAM_CONFLICT_DETECTION=1`) |
| POST | `/v1/conflicts/{id}/resolve` | `{"keep":"newer\|older\|both"}` 让冲突由人确认 |
| PATCH | `/v1/facts/{id}` | 改事实 `{"object":"...","sensitive":true}` |
| DELETE | `/v1/facts/{id}` | 删一条 |
| GET | `/v1/audit?limit=60` | 记忆体检：逐条列出值得修的事实（机器 token、空值、未归约断言、孤立实体等）以及按槽分组的 `slot_overflow`；只读，不改任何数据 |
| POST | `/v1/facts/clear-slot` | **不可逆**：`{"subject":"user","predicate":"occupation","expect_count":84}` 硬删除该 `(subject, predicate)` 槽上的全部 live 事实。用于处理 `slot_overflow`；`expect_count` **必填**，是乐观并发校验（与体检时的条数不一致则整体拒绝、不删除任何行）——无保护的批量删除是这个端点唯一不该提供的形态，因此没有省略它的写法。已被 supersede 的历史事实、以及 `source="user"`（你亲手写过或就地改过的）事实都不在删除范围内 |
| GET | `/v1/export?include_sensitive=false` | 安全结构化导出：仅非敏感 facts + graph；不含画像、摘要、原始对话 |
| POST | `/v1/forget` | 需 `{"confirm":true}`；清空该 key 的全部记忆（不可逆） |

## 5. 控制台（可视化）
浏览器开 **`<Base URL>/ui/`** → 输入你的 key → 看「画像 / 会话结论 / 事实管理 / 时间线 / 关系图谱 / 记忆问答 / 冲突待确认 / 记忆体检」。

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
