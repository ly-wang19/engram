# Engram 个人分身：记忆与治理底座

这份文档界定 Engram 当前个人分身能力的实际边界、安全流程和部署要求。它描述的是已落地的
记忆/治理原语，不是“一个可以完全自主代替你的数字人已经完成”的声明。

## 1. 现在已经有什么

Engram 把分身的“记得你”与“有权代你做什么”分开：

- 长期记忆：Episode、Fact、双时态知识图、身份合并、冲突链、来源追溯与可验证擦除。
- Twin Contract：版本化保存 owner 的 goals、principles、boundaries 和人工确认策略，保留修订历史。
- Capability Registry：以 `capability + permission + exact scope` 授权，无 grant 时默认拒绝，支持过期和撤销。
- Decision/Audit：为每个具体资源产生短时决策，高风险或外部写入可要求 owner 确认，执行结果持久化审计。

当前不包含通用自主 planner、定时任务、背景运行的行动循环、日历/邮件/支付的真实执行器，也不会用模型的自述
代替人类确认。外部连接器和受信执行器必须由部署方另行提供，并严格执行本文的 decision 检查协议。

## 2. 四个信任域

| 主体 | 凭据/入口 | 允许 | 不允许/不保证 |
|---|---|---|---|
| agent/app/模型调用方 | `ENGRAM_API_KEYS` | 记忆读写、prompt-safe contract、脱敏 grant 视图、authorize、decision 重校验、record | 不能修订契约、grant/revoke、查看 credential reference 或代替 owner confirm |
| 人类 owner 控制面 | `ENGRAM_OWNER_KEYS` | 完整契约/历史、契约修订、grant/revoke、完整 capability 视图、confirm | owner key 本身不自动变成普通记忆 API key，也不执行外部动作 |
| 受信执行器 | 部署方实现 | 执行前查 `executable`，从 Vault/Keychain 解析凭据，调用外部 API，成功后 record | Engram 不能强制一个绕过它的执行器；执行器也不应盲信 prompt 对风险级别的标注 |
| MCP 工具面 | `engram_*` tools | 读 prompt-safe contract/脱敏 grant，请求 authorize，记录外部 executor 回报 | **不暴露 owner 治理写**：无 contract edit、grant/revoke、owner confirm；authorize/record 仅写决策/审计，不改治理策略也不执行 |

### 2.1 两类 key 必须分离

生产配置形式：

```bash
export ENGRAM_API_KEYS='personal:replace-with-agent-key-at-least-32-chars'
export ENGRAM_OWNER_KEYS='personal:replace-with-a-different-owner-key-32-chars'
```

- 左侧 tenant 名必须一致，这样两个 key 才指向同一个记忆/治理 namespace。
- 右侧 key 字符串必须不同。只要 `ENGRAM_OWNER_KEYS` 与 `ENGRAM_API_KEYS` 交集非空，owner key 配置即被视为无效，owner 路由返回 503。
- owner key 只放在独立的人类审批 UI/私有终端，不注入模型 prompt、agent 环境、MCP 配置或日志。
- 两个环境变量都支持 `tenant:key-new,tenant:key-old` 方式的同租户轮换；轮换期也不得在两个平面复用 key。

仓库的 `deploy/docker-compose.yml` 会转发 `ENGRAM_OWNER_KEYS`。实际部署时必须替换
`deploy/.env` 里的两个示例 key，并保证 owner key 只进人类审批终端，不进 agent 运行时。

## 3. Contract 与 Capability 的可见性

### 3.1 普通 contract 只是 prompt-safe 视图

`GET /v1/twin/contract` 仅返回 `contract_version` 和 `model_context`：

- goals：`title`、`description`、`status`；
- principles：`name`、`statement`；
- boundaries：仅 `model_visible=true` 的自然语言 `description`。

它不返回 boundary effect、capability/scope/minimum permission、来源、confirm 开关、grants 或 credential
reference。这些执行控制只存在受信服务端和 owner 视图，不依赖 prompt 保密来执行。

owner 使用下列接口：

- `GET /v1/twin/control/contract`：当前完整契约。
- `GET /v1/twin/control/contract/history?limit=100`：修订历史，最新版在前，`limit` 必须在 1–1000。
- `PUT /v1/twin/contract`：owner-only 修订；每次成功修订都生成新版本，旧版本保留在历史中。

### 3.2 Grant 是最小权限，不是 prompt 建议

permission 从低到高为 `observe < draft < execute`；高级 grant 包含低级请求。scope 是严格的斜杠分段路径：

- `*` 只匹配一个完整 segment；末尾 `**` 匹配零个或多个完整 segment。
- `accounts/personal/**` 匹配 `accounts/personal/mail/inbox`，不匹配 `accounts/personal-private/...`。
- 资源中的 wildcard、`..`、百分号编码分隔符、反斜杠、控制字符和非规范斜杠均 fail closed。
- 无 active matching grant 默认 `denied`。owner boundary 只能继续缩小权限或增加确认门，不能创造 grant 未给予的权力。

`GET /v1/twin/capabilities` 给 agent 看见 capability、permission、scope、时间与撤销状态，但用
`credential_configured` 替代 `credential_ref`。owner 通过 `GET /v1/twin/control/capabilities` 看完整引用，并且只有 owner
能 `POST /v1/twin/capabilities` 或 `POST /v1/twin/capabilities/{grant_id}/revoke`。

### 3.3 Credential reference 永远不是秘密本身

```json
{"provider":"macos-keychain","key":"engram/calendar-primary"}
```

这个对象只告诉受信执行器“到哪个 provider 用哪个标识取凭据”。Engram 不保存密码、OAuth token、
API secret、私钥或恢复码。真实秘密应由 macOS Keychain、KMS、Vault 或等效秘密管理器保管；不要把秘密
塞进 `provider`、`key`、provenance、outcome、记忆正文或日志。

## 4. 决策的两阶段生命周期

1. agent 用精确的 `capability`、`permission`、规范化 `resource` 调用 `POST /v1/twin/authorize`。
2. Engram 以当前 contract 和 registry 评估，持久化 request + decision，返回 `denied`、`requires_confirmation` 或 `allowed`。
3. authorize **永远不执行**，HTTP request schema 也不接受可用来授权的 `human_confirmed`。
4. 如需确认，人类 owner 必须在待定窗口内用独立 owner key 调用
   `POST /v1/twin/decisions/{decision_id}/confirm`。confirm 会用当前 contract/grant/boundary 重新评估，而不是盲目翻转旧结果。
5. 受信 executor 在调外部 API 前立即 `GET /v1/twin/decisions/{decision_id}`，只在 `executable=true`
   且 `executed=false` 时继续。
6. executor 完成真实动作后，调用 `POST /v1/twin/actions/record` 追加 outcome/provenance。record 会再次检查
   decision，同一 decision 不能重复记录，但它本身仍不会执行外部动作。

### 4.1 300 秒窗口是上限

对于 active matching grant，决策的 `valid_until` 最多是 `decided_at + 300` 秒，并受 grant `expires_at` 更早截断。
`denied` 决策不可执行。`requires_confirmation` 必须在本身窗口内被 owner 确认；确认时会重新生成一个
受当前 grant 过期时间限制的短时 `allowed` 窗口。

GET decision 的 `executable` 不只看字面 status，还会检查：

- 当前时间没有超过 `valid_until`；
- 决策记录的 `policy_version` 仍等于当前 contract version；
- 当初匹配的 grant 仍 active、未被撤销，且当前 boundary 重评估仍允许；
- 需人工确认的请求确实有 owner-plane `confirmed_at`。

所以 executor 不应缓存 `allowed`、不应把 decision 当 API token 长期使用，也不应在执行后忽略 `record`。

## 5. 可复制的 curl 流程

### 5.1 启动两个信任平面

```bash
export AGENT_KEY='replace-with-a-different-agent-key-32-chars'
export OWNER_KEY='replace-with-a-different-owner-key-32-chars'
export ENGRAM_API_KEYS="personal:${AGENT_KEY}"
export ENGRAM_OWNER_KEYS="personal:${OWNER_KEY}"
python3 -m uvicorn engram.server.app:app --host 127.0.0.1 --port 8000
```

另开一个终端：

```bash
export B='http://127.0.0.1:8000'
export AGENT_KEY='replace-with-a-different-agent-key-32-chars'
export OWNER_KEY='replace-with-a-different-owner-key-32-chars'
```

### 5.2 owner 修订契约

```bash
curl -fsS -X PUT "$B/v1/twin/contract" \
  -H "Authorization: Bearer $OWNER_KEY" -H 'Content-Type: application/json' \
  -d '{
    "goals":[{"title":"Protect focused work","description":"Keep synthetic mornings meeting-free","provenance":["owner:setup"]}],
    "principles":[{"name":"Reversibility","statement":"Prefer reversible actions","provenance":["owner:setup"]}],
    "boundaries":[{"description":"Ask before changing the calendar","effect":"require_confirmation","capability":"calendar","scopes":["calendars/personal/**"],"minimum_permission":"execute","model_visible":true}],
    "provenance":["owner:contract-v2"]
  }'

# agent 只能看到 prompt-safe model_context。
curl -fsS "$B/v1/twin/contract" -H "Authorization: Bearer $AGENT_KEY"

# owner 可查完整契约和历史。
curl -fsS "$B/v1/twin/control/contract" -H "Authorization: Bearer $OWNER_KEY"
curl -fsS "$B/v1/twin/control/contract/history?limit=20" \
  -H "Authorization: Bearer $OWNER_KEY"
```

### 5.3 owner grant → agent authorize → owner confirm → executor 重校验 → record

```bash
GRANT_JSON=$(curl -fsS -X POST "$B/v1/twin/capabilities" \
  -H "Authorization: Bearer $OWNER_KEY" -H 'Content-Type: application/json' \
  -d '{"capability":"calendar","permission":"execute","scopes":["calendars/personal/**"],"credential_ref":{"provider":"macos-keychain","key":"engram/calendar"},"provenance":["owner:grant-v1"]}')
printf '%s\n' "$GRANT_JSON"
GRANT_ID=$(printf '%s' "$GRANT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["grant"]["id"])')

PENDING_JSON=$(curl -fsS -X POST "$B/v1/twin/authorize" \
  -H "Authorization: Bearer $AGENT_KEY" -H 'Content-Type: application/json' \
  -d '{"capability":"calendar","permission":"execute","resource":"calendars/personal/events/demo-42","description":"Create an owner-visible demo event","external_write":true}')
printf '%s\n' "$PENDING_JSON"
DECISION_ID=$(printf '%s' "$PENDING_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["decision"]["id"])')

curl -fsS -X POST "$B/v1/twin/decisions/$DECISION_ID/confirm" \
  -H "Authorization: Bearer $OWNER_KEY"

LIVE_JSON=$(curl -fsS "$B/v1/twin/decisions/$DECISION_ID" \
  -H "Authorization: Bearer $AGENT_KEY")
printf '%s' "$LIVE_JSON" | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p["executable"] and not p["executed"]; print("authorization current")'

# 仅在上一行通过后，受信 executor 才可以用 credential_ref 从 Keychain 取凭据并调用真实 API。
# ./trusted-calendar-executor --decision "$DECISION_ID"

# 仅在真实动作成功后记录；建议省略 executed_at，由 Engram 以当前时间检查窗口。
curl -fsS -X POST "$B/v1/twin/actions/record" \
  -H "Authorization: Bearer $AGENT_KEY" -H 'Content-Type: application/json' \
  -d "{\"decision_id\":\"$DECISION_ID\",\"outcome\":\"owner-approved demo event created\",\"provenance\":[\"executor:calendar-demo\"]}"

# owner 可随时撤销 grant；尚未执行的旧 decision 会在下次 GET 时变为 executable=false。
curl -fsS -X POST "$B/v1/twin/capabilities/$GRANT_ID/revoke" \
  -H "Authorization: Bearer $OWNER_KEY"
```

## 6. Provenance 擦除与存储边界

### 6.1 Fact 擦除是 preview → confirm

```bash
export FACT_ID='fact-id-from-v1-memories'

# 只预览：返回 confirmation_required=true 和 impact counts，不写数据。
curl -fsS -X DELETE "$B/v1/facts/$FACT_ID" \
  -H "Authorization: Bearer $AGENT_KEY"

# 确认后才执行 provenance cascade。
curl -fsS -X DELETE "$B/v1/facts/$FACT_ID?confirm=true" \
  -H "Authorization: Bearer $AGENT_KEY"
```

从 source episode 自动抽取的 fact 不是孤立字符串。确认擦除会删除目标 fact、其 source episode、同一
source 的 sibling derivations、相关 graph relation/conflict/working 引用，并清理存活对象里的悬空 provenance/supersedes。
没有 episode provenance 的手工 fact 则只删目标 fact。

### 6.2 Session 擦除

```bash
# 无确认是 no-op。
curl -fsS -X POST "$B/v1/sessions/erase" \
  -H "Authorization: Bearer $AGENT_KEY" -H 'Content-Type: application/json' \
  -d '{"session_id":"agent:synthetic-demo","confirm":false}'

# 明确确认后，擦除该 session 的 raw episode、派生 fact 和 working/conflict 状态。
curl -fsS -X POST "$B/v1/sessions/erase" \
  -H "Authorization: Bearer $AGENT_KEY" -H 'Content-Type: application/json' \
  -d '{"session_id":"agent:synthetic-demo","confirm":true}'
```

### 6.3 `storage_verified` 不等于物理介质抹除

擦除回执中：

- `verified=true`：对当前内存层完成标识符与悬空引用扫描。
- `storage_verified=true`：提交后从 canonical snapshot 新鲜重开，再验证声明范围内的对象不可达。

该回执不覆盖旧 LanceDB fragments、未回收文件页、SSD flash translation layer、磨损均衡、APFS/VM snapshot、
Time Machine、云盘版本历史、备份、已导出副本或已发送到第三方模型/日志的内容。LanceDB 当前对 live table
做逻辑删除，不声称历史 fragment 已逐字节消失。

0700/0600 文件权限是本机访问控制，不是应用层静态加密。个人敏感记忆必须放在 FileVault、LUKS/dm-crypt 或
独立加密 APFS volume/disk image 等加密边界内，并把 canonical store、Lance base、swap/休眠文件和备份一起纳入策略。
详见 [`storage-privacy-boundary.zh-CN.md`](storage-privacy-boundary.zh-CN.md)。

## 7. 威胁边界

| 风险 | 当前缓解 | 仍需部署方负责 |
|---|---|---|
| prompt injection 企图修改权限 | 模型只看 prompt-safe contract；policy/grant 在 prompt 外以确定性代码执行 | executor 必须只信服务端 decision，不信模型声称“已授权” |
| agent key 泄漏 | 攻击者不能修改 Twin Contract/grant/revoke 或 owner-confirm | **普通 key 仍可以读写甚至确认擦除该 namespace 的记忆**；应最小化暴露、轮换 key，并在网关分离记忆管理路由 |
| owner key 泄漏 | 与 agent key 隔离，不进 MCP/prompt | 攻击者可更改契约和 grants 并确认高风险动作；应放入人类审批平面、短会话、MFA/网络 ACL |
| 请求把高风险误标为低风险 | contract 可以用 scope + minimum permission boundary 强制确认 | executor/网关必须根据真实动作独立设定 `high_risk`/`external_write`，不能盲信模型传值 |
| decision 重放/TOCTOU | 最多 5 分钟、执行前重评估 contract/grant/boundary、同 decision 只记录一个 outcome | 外部 API 需要幂等键/去重；执行与 record 之间的崩溃不能由 Engram 自动回滚 |
| 请求内容在授权后被替换 | decision 绑定 capability、permission、canonical resource、description 和风险标志 | 当前不对整个外部 API payload 做签名；executor 必须检查待执行 payload 与已批准资源/意图一致，高风险系统应另外绑定 payload hash |
| 伪造 action outcome | record 前再验证 decision，同 decision 只能记一次 | outcome/provenance 是执行器自报，不是密码学回执；网关应把 record 限制给执行器，必要时附外部系统签名回执 |
| 本地审计篡改或整库回滚 | SQLite 事务、`store_id`/`commit_id` 和 generation 能识别并发旧写及 DB/manifest 错配 | 当前没有签名、远端锚点或 WORM 日志；拥有存储写权限的攻击者仍可一致地回滚整套 DB+manifest，不能把本地记录当作防篡改证据 |
| 审计数据持续增长 | 决策、执行结果和 contract 历史随 canonical snapshot 持久化 | 当前 beta 把这些对象保存在私有 SQLite `state` JSON 中，尚无独立追加表、归档或 retention policy；长期高频 executor 部署需监控体积并在后续 schema 中迁移 |
| 执行器绕过 Engram | Engram 给出可校验协议和审计状态 | 凭据网关/连接器必须把 GET decision 校验作为真实外部 API 前置条件；否则 Engram 无法强制 |
| 交通、日志与第三方副本 | `/v1/*` 返回 `Cache-Control: no-store`，credential 只保存引用 | 生产必须用 TLS，禁止 header/body 日志泄密，管理模型供应商、导出、备份和同步副本 |
| 本机或介质泄密 | owner-only 文件权限、canonical 存储校验 | FileVault/加密卷、管理员/root 边界、备份加密与介质处置仍由部署方负责 |

此外，Engram 当前的 owner-plane 分离仅针对个人分身治理路由，并没有将所有记忆管理 API 转移到 owner key。如需将
memory read/write/delete 也细分权限，应在反向代理/API gateway 层做路由 ACL，不要把现有双 key 设计理解为全部 API 的 RBAC。

## 8. MCP 的准确边界

MCP 对 agent 暴露：

- `engram_get_twin_contract`：prompt-safe 契约语义。
- `engram_list_capabilities`：不含 credential reference 的 grant 视图。
- `engram_authorize_twin_action`：永远以 `human_confirmed=false` 评估，只返回决策，不执行。
- `engram_record_twin_action`：仅记录另一个受信 executor 的 outcome；MCP 不允许选择 `executed_at`，服务端以当前时间重校验。

MCP 不提供 contract edit、contract history/full contract、capability grant/revoke、credential reference 查看或 owner
confirmation。因此它没有 owner 治理写入能力。authorize/record 会追加决策/审计状态，但不能提升自身权限、
修改 owner 策略或调用外部工具。

## 9. 评测该如何解读

```bash
python eval/twin_eval.py
```

该零 key、无外部依赖 harness 跑 16 个稳定不变量场景：默认拒绝、segment scope 绕过、外部写确认、
过期/撤销、契约版本、持久化审计和 provenance 擦除。`16/16` 仅表示这些确定性安全/治理不变量通过：

- 它不是 LongMemEval/LOCOMO/BEAM 之类模型记忆质量 benchmark；
- 不能从中推导“世界第一”、SOTA、现实任务成功率或自主分身已完成；
- 不测试真实邮件/日历执行器、网络故障、人类审批 UI 或部署方的 Keychain/Vault 集成。

它的用途是回归防护和可追溯分母，不是公开 benchmark headline。

## 10. 个人部署检查单

- agent key 与 owner key 完全不同；owner key 不进 prompt/MCP/agent 环境，两者正确映射到同 tenant。
- 无需的 capability 不 grant；使用最小 permission、最窄 exact scope 和可接受的 `expires_at`。
- 对删除、发送、付费、公开发布等动作增加 owner boundary，不只依赖请求自报 `high_risk`。
- executor 每次动作前 GET decision，检查 `executable=true` 且 `executed=false`，使用外部幂等键，成功后立即 record。
- credential 本体只在 Keychain/Vault/KMS；Engram 仅保存无秘密的 reference。
- 生产使用 TLS、禁止 authorization/body 日志，在网关为记忆管理路由增加额外 ACL。
- canonical store、Lance base、备份与休眠/swap 放入 FileVault/加密卷；检查 snapshot/同步保留策略。
- 擦除前读 impact preview；擦除后把 `storage_verified` 解读为 canonical snapshot 验证，而不是物理介质证明。
