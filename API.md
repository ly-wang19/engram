# Engram 记忆服务 · 接口文档

一套多租户的长期记忆 HTTP API：**每个 API key 就是一个独立的记忆空间**（互相隔离）。把对话/记忆灌进去，再用检索接口测效果。配套一个浏览器控制台可视化查看。

---

## 0. 两种用法（任选）

**A. 直接用已部署的服务（最快，零搭建）**
- Base URL：`http://42.193.220.197:8456`
- 自己起一个 key 当命名空间（例如 `demo-test`），数据互相隔离。
- 适合快速测；脱敏数据会存在该服务器上。

**B. 自己部署（数据完全本地）**
```bash
git clone https://github.com/ly-wang19/engram-memory.git
cd engram-memory
pip install "engram-memory[server]"          # 或 pip install -e ".[server]"
export ENGRAM_EMBEDDER=bge-small              # 本地嵌入，无需 key
export ENGRAM_LLM=volcano:doubao-seed-1-6-flash-250615   # 抽取/答题用，需在 .env 配 ARK_API_KEY
export ENGRAM_ANSWERER=volcano:doubao-seed-2-0-pro-260215 # 答题模型（可选，默认同上）
export ENGRAM_OPEN=1                          # 开放模式：key 即命名空间
uvicorn engram.server.app:app --host 0.0.0.0 --port 8456
# 控制台：http://localhost:8456/ui/
```
> 想做真正的鉴权隔离：去掉 `ENGRAM_OPEN`，改设 `ENGRAM_API_KEYS="alice:sk-a,bob:sk-b"`。

---

## 1. 鉴权
所有 `/v1/*` 接口都要带：
```
Authorization: Bearer <你的key>
Content-Type: application/json
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
{ "query": "我最喜欢哪个歌手", "lean": true, "n_chunks": 4 }
```
返回：
```json
{ "answer": "你最喜欢周杰伦。",
  "context": "USER PROFILE:...\nFACTS:...\nRELEVANT CONVERSATIONS:...",  // 喂给模型的精炼上下文
  "tokens_est": 9958,        // 精炼上下文 token
  "full_tokens": 81408 }     // 整段历史 token（full-context 基线）→ 省 8×
```

## 4. 查看 / 管理

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/memories` | 全部事实(双时间轴/来源/分类/敏感)+ 原始对话 + 摘要 |
| GET | `/v1/profile/structured` | 结构化用户画像(基本信息/偏好/习惯) |
| GET | `/v1/conflicts` | 待确认的疑似冲突(LLM 检测，需开 `ENGRAM_CONFLICT_DETECTION=1`) |
| POST | `/v1/conflicts/{id}/resolve` | `{"keep":"newer\|older\|both"}` 让冲突由人确认 |
| PATCH | `/v1/facts/{id}` | 改事实 `{"object":"...","sensitive":true}` |
| DELETE | `/v1/facts/{id}` | 删一条 |
| GET | `/v1/export?include_sensitive=false` | 全量导出 JSON(可排除敏感) |
| POST | `/v1/forget` | 清空该 key 的全部记忆 |

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
