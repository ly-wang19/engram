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
> 如启用个人分身治理控制面，还必须配置独立的
> `ENGRAM_OWNER_KEYS="alice:another-owner-key"`。owner key 与 `ENGRAM_API_KEYS` 中的任何 key
> 字符串都不得复用，否则 owner 配置失效且 readiness fail closed。附带的 Compose 文件已支持
> 从 `deploy/.env` 转发 `ENGRAM_OWNER_KEYS`。不要把 owner key 配到 agent、MCP 或模型可见环境。

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

个人分身控制面使用两类不可复用的 Bearer key：

- `ENGRAM_API_KEYS`：普通 agent/app 面，用于记忆 API、prompt-safe 分身上下文、请求授权、
  查询决策和记录执行结果。
- `ENGRAM_OWNER_KEYS`：人类 owner 控制面，用于完整契约/历史、契约修订、capability
  grant/revoke、完整 credential reference 查看和待定决策确认。

两类 key 应映射到相同 tenant 名字以共享同一命名空间，但 key 本身必须不同。owner key
不是普通 API key 的“更高权限版”；一个 owner 客户端如果还要读写普通记忆，需要分开保管两个 key。

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
  "owner_control_configured": true,
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
| DELETE | `/v1/facts/{id}` | 先返回 provenance 级联删影响预览；只有 `?confirm=true` 才执行 |
| GET | `/v1/export?include_sensitive=false` | 安全结构化导出：仅非敏感 facts + graph；不含画像、摘要、原始对话 |
| POST | `/v1/forget` | 需 `{"confirm":true}`；清空该 key 的全部记忆（不可逆） |

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

---

## 8. 个人分身治理 API

Engram 当前提供的是**个人分身的记忆与治理底座**：版本化 Twin Contract、默认拒绝的
capability registry、短时授权决策和执行审计。它不是已经完成的自主分身：不包含自主计划循环、
定时器、外部工具执行器，也不会代替人类做高风险确认。详细的生命周期、curl 流程与威胁边界见
[`docs/personal-twin.zh-CN.md`](docs/personal-twin.zh-CN.md)。

### 8.1 路由与凭据

| 方法 | 路径 | 凭据 | 语义 |
|---|---|---|---|
| GET | `/v1/twin/contract` | agent key | 仅 `model_context`：可进 prompt 的 goals/principles/允许显示的 boundary 描述；无治理控制 |
| GET | `/v1/twin/control/contract` | owner key | 完整当前契约及 prompt-safe 视图 |
| GET | `/v1/twin/control/contract/history?limit=100` | owner key | 不可变契约修订历史，新版在前；`limit` 为 1–1000 |
| PUT | `/v1/twin/contract` | owner key | 修订 goals/principles/boundaries/确认策略，产生 `version + 1` |
| GET | `/v1/twin/capabilities` | agent key | 可检查的 grant 视图；隐去 `credential_ref`，只给 `credential_configured` |
| GET | `/v1/twin/control/capabilities` | owner key | 完整 grant 视图，包含 credential reference |
| POST | `/v1/twin/capabilities` | owner key | 新增 scoped grant |
| POST | `/v1/twin/capabilities/{grant_id}/revoke` | owner key | 撤销 grant |
| POST | `/v1/twin/authorize` | agent key | 只生成/持久化决策，`executed` 永远为 `false` |
| POST | `/v1/twin/decisions/{decision_id}/confirm` | owner key | 确认一条尚未过期的 `requires_confirmation` 决策；仍不执行 |
| GET | `/v1/twin/decisions/{decision_id}` | agent key | 执行前重新校验，查看 `executable` 和 `executed` |
| POST | `/v1/twin/actions/record` | agent key | 为外部受信执行器已完成的动作追加 outcome 审计；本路由不执行外部动作 |

`POST /v1/twin/authorize` 的 HTTP schema 没有 `human_confirmed`字段，普通 agent 不能为自己证明“人已确认”；
服务端始终按 `false` 评估。必须由独立 owner key 调用 confirm 路由。`allowed` 也不是长期 token：
`valid_until` 最多为决策时刻后 300 秒，如 grant 更早过期则进一步截短。契约更版、grant
撤销/过期、boundary 变化或超过 `valid_until` 都会让 `executable=false`。受信执行器必须在每次
真实执行前 GET decision 再检查，成功后才调用 actions/record。

`credential_ref` 只是如 `{"provider":"macos-keychain","key":"engram/calendar"}` 的外部
Vault/Keychain 定位标识，不是 token、密码或密钥材料；真实凭据不应进入 Engram 记忆库或 prompt。
`actions/record` 中的 outcome/provenance 也是执行器回报，不是 Engram 对外部系统成功的密码学证明；
需要强审计时，应在网关限制 record 路由，并保存外部系统的签名回执/幂等键。

### 8.2 可复制最小流程

以本地 Uvicorn 为例，先用两个不同强 key 启动服务：

```bash
export AGENT_KEY='replace-with-a-different-agent-key-32-chars'
export OWNER_KEY='replace-with-a-different-owner-key-32-chars'
export ENGRAM_API_KEYS="personal:${AGENT_KEY}"
export ENGRAM_OWNER_KEYS="personal:${OWNER_KEY}"
python3 -m uvicorn engram.server.app:app --host 127.0.0.1 --port 8000
```

在另一终端执行：

```bash
export B='http://127.0.0.1:8000'
export AGENT_KEY='replace-with-a-different-agent-key-32-chars'
export OWNER_KEY='replace-with-a-different-owner-key-32-chars'

# 1) owner 建立一条最小 calendar execute grant。credential_ref 只是外部密钥库定位符。
GRANT_JSON=$(curl -fsS -X POST "$B/v1/twin/capabilities" \
  -H "Authorization: Bearer $OWNER_KEY" -H 'Content-Type: application/json' \
  -d '{"capability":"calendar","permission":"execute","scopes":["calendars/personal/**"],"credential_ref":{"provider":"macos-keychain","key":"engram/calendar"},"provenance":["owner:setup"]}')
printf '%s\n' "$GRANT_JSON"

# 2) agent 请求外部写授权；此调用不会创建日历事件。
PENDING_JSON=$(curl -fsS -X POST "$B/v1/twin/authorize" \
  -H "Authorization: Bearer $AGENT_KEY" -H 'Content-Type: application/json' \
  -d '{"capability":"calendar","permission":"execute","resource":"calendars/personal/events/demo-42","description":"Create an owner-visible demo event","external_write":true}')
printf '%s\n' "$PENDING_JSON"
DECISION_ID=$(printf '%s' "$PENDING_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["decision"]["id"])')

# 3) 人类 owner 在独立平面确认；确认本身仍不执行。
curl -fsS -X POST "$B/v1/twin/decisions/$DECISION_ID/confirm" \
  -H "Authorization: Bearer $OWNER_KEY"

# 4) 受信执行器在动作前立即重新查询；必须 executable=true 且 executed=false。
LIVE_JSON=$(curl -fsS "$B/v1/twin/decisions/$DECISION_ID" \
  -H "Authorization: Bearer $AGENT_KEY")
printf '%s' "$LIVE_JSON" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p["executable"] and not p["executed"]; print("authorization current")'

# 5) 此处由受信 calendar executor 调用真实外部 API。只有真实动作成功后才记录 outcome。
# ./trusted-calendar-executor --decision "$DECISION_ID"
curl -fsS -X POST "$B/v1/twin/actions/record" \
  -H "Authorization: Bearer $AGENT_KEY" -H 'Content-Type: application/json' \
  -d "{\"decision_id\":\"$DECISION_ID\",\"outcome\":\"owner-approved demo event created\",\"provenance\":[\"executor:calendar-demo\"]}"
```

### 8.3 擦除回执的边界

`DELETE /v1/facts/{id}` 不带确认时只返回 `impact` 计数，不修改数据；只有
`DELETE /v1/facts/{id}?confirm=true` 才会删除该 fact、其原始 source episode、同一 source 派生的
sibling facts 及相关派生状态。`POST /v1/sessions/erase` 亦必须传
`{"session_id":"...","confirm":true}`，它擦除整个 source session 及其派生记忆。

回执里的 `verified=true` 是当前内存层校验；`storage_verified=true` 表示重新打开
canonical snapshot 后已校验目标对象不可达。它**不证明** SSD/APFS/Time Machine/云同步历史或
LanceDB 旧 fragment 已物理抹除。Engram 的 0700/0600 权限也不是静态加密；个人敏感记忆应放在
FileVault、LUKS 或独立加密卷内，并单独管理备份、snapshot 与同步保留策略。
