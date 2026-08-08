# Engram（印迹）

**[English](README.md) | 🌐 中文**

**一个面向 LLM 智能体的开源长期记忆引擎 —— 只围绕一个原则：我们公布的每一个数字，你都能自己复现。**

**📄 [论文 — arXiv:2606.09900 →](https://arxiv.org/abs/2606.09900)** —— 《Less Context, More Accuracy：面向 LLM 智能体的双时态记忆引擎》。

**🎬 [在线动画演示 →](https://ly-wang19.github.io/engram/)** —— 60 秒看懂它怎么工作(小白友好)。

**🔌 [在线控制台 →](http://42.193.220.197:8456/ui)** —— 打开后输入体验 key `1`,即可端到端浏览一份完整的公开记忆(画像、事实、时间线、图谱、问答)。这是公开演示，请勿提交隐私数据。

Engram 让 LLM 智能体拥有跨会话的、可查询的持久记忆：它记录发生过什么、提炼出原子事实、追踪这些事实
随时间的变化（双时间轴 bi-temporal）、在不丢历史的前提下解决矛盾，并用「语义 + 词法 + 图 + 时近」的
混合检索把最相关的上下文取出来。

> 状态：**0.1.0 beta · 单节点自托管交付版**。端到端流程**零配置**即可跑（不需要 API key，不需要任何
> 服务），生产部署则在未配置凭据时默认失败关闭。交付边界见
> [`docs/commercial-release-0.1.0.zh-CN.md`](docs/commercial-release-0.1.0.zh-CN.md)，安全策略见
> [`SECURITY.md`](SECURITY.md)，基准方法与原始日志见 [`RESULTS.md`](RESULTS.md)。

## 为什么要再造一个记忆系统？

这个领域有两个真实的缺口，我们两个都打：

1. **大多数记忆系统在准确率上打不过"把全部历史塞进上下文"这个笨基线** —— 它们赢在成本，不赢在正确率。
   我们在每张结果表里**都列出 full-context 基线**，让你一眼看到我们到底处在什么位置。
2. **每家厂商的基准数字都跑在各自不同、不可复现的 harness 上。** 同一个系统在不同来源里能查到 58% / 66% /
   92% 三个数字；不同论文给出互相矛盾的排名。我们只提供**一套中立的 harness**，内置官方判分器，并
   公开每一题的原始日志。

在一个数字全靠自说自话的领域里，**「那个谁都能验证的记分牌」本身就是壁垒。**

## 结果 —— LongMemEval_S（500 题，官方判分）

在真实的 [LongMemEval_S](https://github.com/xiaowu0162/LongMemEval) 基准上测得（500 题，每题约 50 个
会话 / 约 11.5 万 token 的干扰上下文），用**官方分类判分 prompt**评分。作答器 **doubao-seed-2.0-pro**、
判分器 **DeepSeek-V3.2** —— 标准、严格的判分器,所以这是公平的数字,不是挑个宽松的。

**头条系统是 `engram_lean`:它从检索到的一小片作答,从不读全部历史。** 这才是记忆系统的真正考验,
也是本项目的核心论点(用零头的 token 在准确率上打赢全文):

| 系统 | 总分 | 平均上下文 token | 端到端延迟（p50 / p95） | 报错 |
|---|---:|---:|---:|---:|
| **Engram**（`engram_lean`） | **79.0%** | **7,283** | 93.6s / 173.7s | 0 / 500 |
| 裸塞全文基线（同一次运行、作答器和判分器） | 76.0% | 79,241 | 14.5s / 60.1s | 0 / 500 |

在 canonical 同场联合运行中，**Engram 的准确率点估计为 +3.0 分，上下文 token 减少 10.9 倍**
（7,283 vs 79,241）。本次端到端延迟没有取胜；延迟包含远程作答调用，我们不把差异归因于检索。
分项（`engram_lean`，全 500 题）：

| 类别 | 得分 | 题数 |
|---|---:|---:|
| 单会话-助手 | 100.0% | 56 |
| 知识更新 | 91.7% | 72 |
| 拒答 | 90.0% | 30 |
| 单会话-用户 | 84.4% | 64 |
| 时间推理 | 70.9% | 127 |
| 多会话 | 70.2% | 121 |
| 单会话-偏好 | 56.7% | 30 |

**它的定位：**这次全 500 题的 paired 运行支持 token 效率结论和正向的准确率点估计，
但不支持“统计显著领先”（paired McNemar `p=0.195`；差值的 bootstrap 95% CI 为
`[-1.2, +7.2]` 分）。同一作答器、同一严格判分器、每题留痕、不挑切片。canonical 日志与历史独立运行见
[`RESULTS.md`](RESULTS.md)。**这些结果不足以证明“世界第一”或领域领先。**

## 工作原理

Engram 是一个**双过程（dual-process）**记忆系统，仿照人脑的 System-1 / System-2 分工：一条永不被 LLM
阻塞的快写入路径，和一条在离线做重型结构化的慢固化路径。

```mermaid
flowchart TB
    ADD([add 写入消息]) --> S1
    subgraph S1 [System-1 · 热写入路径 · 不调 LLM]
        direction LR
        S1a[追加无损 Episode] --> S1b[身份解析<br/>跨会话/设备] --> S1c[轻量嵌入 + 入队]
    end
    S1 -. 异步队列 .-> S2
    subgraph S2 [System-2 · 异步固化 · 秒级]
        direction LR
        S2a[抽取原子事实 Fact] --> S2b[构建双时间轴图谱<br/>实体 + 关系] --> S2c[低成本冲突检测<br/>非破坏式失效] --> S2d[显著度打分 + 衰减]
    end
    S2 --> TM
    subgraph TM [类型化记忆 · 每种类型有自己的存储与检索策略]
        direction LR
        TMa[(情节记忆)]
        TMb[(语义记忆<br/>双时间轴图谱)]
        TMc[(画像 /<br/>身份)]
        TMd[(程序性记忆)]
    end
    TM --> R
    Q([search 查询]) --> R
    subgraph R [读取路径 · 混合检索]
        direction TB
        Ra[多跳问题分解] --> Rb[并行检索:<br/>稠密向量 + BM25 词法 + 图 n 跳 + 时近/显著度]
        Rb --> Rc[RRF 倒数排名融合 + 可选重排] --> Rd[双时间轴 as-of 时点过滤] --> Re[拒答闸门] --> Rf[组装带日期、带溯源的上下文]
    end
    Rf --> OUT([可直接作答的上下文])
```

**写入路径（System-1）**：追加一条无损情节、跨会话/设备解析身份、嵌入并入队 —— 关键路径上不调 LLM。
**固化路径（System-2）**：异步运行，抽取原子的 `(主语, 谓语, 宾语)` 事实、构建知识图谱、
解决矛盾。**读取路径**：分解问题、并行走四条互补通道检索，把正证据信号用 RRF 融合，并把时近/显著度作为先验（可选重排），做时点过滤、组装出带日期与溯源的上下文。算法层契约见
[`docs/algorithm-architecture.md`](docs/algorithm-architecture.md)。

### 它的独特之处

| # | 设计选择 | 为什么重要 |
|---|---|---|
| 1 | **双时间轴事实** —— 每个事实同时带*有效时间*（在现实中何时为真）**和**\*事务时间\*（我们何时得知） | 让"我们在 T 时刻知道什么？"（`as_of`）和知识更新成为**一等公民**，而非事后补丁。canonical 运行的知识更新为 91.7%、时间推理为 70.9%；单个组件的因果贡献仍需消融实验确认。 |
| 2 | **非破坏式冲突解决** —— 被推翻的事实是*失效*（`invalid_at` + `supersedes` 链），而非删除 | 没有静默的记忆损坏。每个事实都能回答"它从哪来？""它替换了谁？"—— 完整溯源 + 审计轨迹。 |
| 3 | **低成本冲突检测** —— 槽位匹配 + 嵌入/NLI 启发式，**仅在**模糊时才升级到 LLM | 拿到生产级的时间正确性，**却不必每个事实都调一次 LLM** —— 规模化下的成本优势。 |
| 4 | **混合检索** —— 稠密语义 + BM25 词法 + 图邻近作为正证据融合，时近/显著度作为先验 | 没有单一检索器能赢遍所有场景。**已验证结论：事实 + 原始片段，强于任何单独一种** —— 事实补充冲突已解/时间信号，片段找回丢失的细节。 |
| 5 | **双过程分工** —— 快写入、异步固化 | 建图、去重、冲突解决都在关键路径之外进行；读取延迟要进 harness 测量后才公开宣称。 |
| 6 | **一切可插拔** —— LLM / 嵌入器 / 向量库 / 图库都在接口背后，**带零依赖离线兜底** | `python scripts/check_zero_setup.py` **不需要任何 API key、任何服务**即可跑；安装测试依赖后用 `pytest` 覆盖完整单元测试。一行配置即可换上 BGE / LanceDB / Kuzu / 任意 LLM。 |
| 7 | **可复现的 harness** —— 一套中立评测、内置官方判分、每张表都带 full-context 基线、公开原始日志 | 在一个人人数字都被质疑的领域里，**成为那个谁都能验证的记分牌**才是真正的护城河。 |

完整数据模型与冲突解决规则见 [`engram/types.py`](engram/types.py) 与 [`engram/consolidate/`](engram/consolidate/)。

## 快速开始（零配置，不需要 API key）

```bash
python examples/quickstart.py
# 或者安装后：
engram-quickstart
```

用离线确定性兜底（哈希嵌入器、规则抽取器、内存存储）跑完整流程 —— 写入 → 固化 → 检索。真实后端
（LanceDB、Kuzu、LiteLLM、BGE）通过同一套接口接入：`pip install "engram-memory[all]"`。
使用 LanceDB 时，`data_path` 是私有基目录，Engram 会为每个 canonical snapshot 派生 owner-only
namespace 并拒绝不安全复用。文件权限不是加密，Lance 逻辑删除也不是物理擦除保证；详见
[存储与隐私边界](docs/storage-privacy-boundary.zh-CN.md)。

```python
from engram import Memory

mem = Memory()
mem.add("My name is Wei and I work at Tencent.", user_id="u1")
mem.add("Actually I just switched jobs — I now work at Moonshot AI.", user_id="u1")
mem.consolidate()                      # System-2：抽取事实、建图、解决冲突

print(mem.search("Where does Wei work?", user_id="u1").answer())
# -> "Moonshot AI"（被推翻的旧事实是失效，而非删除 —— 历史被完整保留）
```

## 个人分身底座（所有者控制）

Engram 现在不只能“回忆”，也提供个人 AI 分身的治理底座：版本化 **Twin Contract**
保存本人确认的目标、原则和边界；默认拒绝的 **Capability Registry** 只能显式授予
`observe` / `draft` / `execute` 权限，且 scope 按完整路径段匹配。credential 字段只保存
keychain/vault 的查找引用；部署方仍须确保真实密钥不进入合同正文、记忆、provenance 或 prompt。

信任边界是刻意分开的：

- 普通 agent key 只能读可注入模型的安全指引、脱敏权限摘要，发起授权请求、执行前复核状态、
  回写执行结果。“授权”本身永远不执行外部动作。
- 修改合同、授予/撤销能力、确认高风险或外部写入，必须使用独立的
  `ENGRAM_OWNER_KEYS="tenant:<另一个强密钥>"`；owner key 不得与 agent key 复用。
- agent 不能自报 `human_confirmed=true`。owner 只能把一个待确认决策升级为允许；该决策 5 分钟过期，
  合同或 grant 变化也会使它失效。
- 删除 fact/session 会沿 provenance 删除原始 Episode 与同源派生事实，落盘后重新打开 SQLite 复核。
  这是 canonical/logical 校验，不代表 SSD、APFS 快照、备份、云同步或 Lance 旧 fragment 被物理抹除。

运行 `python eval/twin_eval.py` 可复核 16 个确定性控制面不变量。16/16 是离线安全回归，**不是**
对外 benchmark 或世界排名证据。详见[个人分身指南](docs/personal-twin.zh-CN.md)、[`API.md`](API.md)和
[存储与隐私边界](docs/storage-privacy-boundary.zh-CN.md)。

这一版是“记忆 + 授权”底座，不是已完成的自主克隆：项目不内置工具执行器、凭据保险库、
语音/形象模型或后台自主性。受信执行器必须在每次行动前确认 `executable=true`，之后再回写结果。

## 怎么调用 / 接入你的应用

Engram 自带完整**接入层**:HTTP API + MCP + JS/TS SDK + OpenAI 兼容,都走同一个多租户核心
（`MemoryService`）。生产模式下，配置的 API key 映射到稳定、互相隔离的租户命名空间；只有显式开发开放
模式才把 key 文本本身当作命名空间。
内置控制台 `/ui` 也体现同一条产品闭环：不含正文的 session status、带 `scope=auto|long|working`
的会话写入、结束会话、session report 审计、分页记忆管理、安全导出和显式确认清空。
跨 Claude Code / Codex / Cursor / 自研 agent 的推荐生命周期见
[`docs/cross-agent-memory.md`](docs/cross-agent-memory.md) 与
[`docs/agent-adapters.md`](docs/agent-adapters.md)；单会话生命周期自测（本地零服务或 HTTP，包含
`agent_status`、`remember`、`close_session`、`session_report`）见
[`examples/cross_agent_lifecycle.py`](examples/cross_agent_lifecycle.py)，双 agent 交接自测见
[`examples/cross_agent_handoff.py`](examples/cross_agent_handoff.py)。如果 agent 需要用指定 Python
环境启动 MCP，可用 `engram-agent-setup --client codex --local --namespace me --python /path/to/python`
生成本地零服务配置，或用 `--api-url ... --api-key ...` 生成 HTTP 服务配置；最快本地路径是
先 `engram-agent-bootstrap --local --dry-run --namespace me --python /path/to/python` 预览，再
`engram-agent-bootstrap --local --namespace me --python /path/to/python` 安装 Codex + 项目 `.mcp.json`、
运行 doctor 验证已写入配置和 MCP runtime，并输出可粘贴进 AGENTS.md 的策略文本；
加 `--install-policy` 可把这段策略写入受管理的 AGENTS.md block，支持备份和卸载。Codex
也支持 `engram-agent-setup --install-codex --dry-run ...` 预览写入，`--install-codex --doctor`
备份后安装并立刻自检真实 MCP stdio 启动链路，`--uninstall-codex` 只移除 Engram 的 MCP 配置块。
Claude Code / Cursor / 项目级 MCP 客户端可用 `engram-agent-setup --install-mcp-json --doctor ...`
以同样的 dry-run、备份、卸载流程管理 `.mcp.json`。

### 直接调用托管 API（零搭建）

Bearer key 随便起一个，它就是你的私有命名空间。完整接口见 [`API.md`](API.md)；浏览器控制台:
**http://42.193.220.197:8456/ui**（体验 key `1`）。

```bash
B=http://42.193.220.197:8456 ; K=my-app          # 任意 key = 你自己的隔离空间

# 1) 灌一条记忆（自动抽取原子事实，按你输入的语言记录）
curl -s -X POST $B/v1/remember -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"content":"我在字节跳动做后端，最喜欢周杰伦。"}'

# 2) 召回：一小片精炼上下文 + 答案 + 省 token 对比
curl -s -X POST $B/v1/recall -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"query":"我最喜欢哪个歌手"}'
# -> {"answer":"你最喜欢周杰伦。","context":"…","tokens_est":120,"full_tokens":1400}
```

### MCP server（给 Claude Desktop / Cursor 加持久记忆）

```bash
pip install "engram-memory[mcp]"
python -m engram.mcp                 # 本地记忆，零外部服务
# 或代理一个运行中的服务： python -m engram.mcp --api-url http://42.193.220.197:8456 --api-key my-app
```
```jsonc
// claude_desktop_config.json
{ "mcpServers": { "engram": { "command": "python", "args": ["-m", "engram.mcp"] } } }
```

`engram_recall` 和 `engram_search` 也支持 `as_of`（epoch 秒）查询某个历史时刻的记忆视图，并支持
`redact_sensitive=true` 生成适合共享/安全注入的上下文。
`engram_agent_status` 适合 agent 开工前做不含正文的接线检查：当前 namespace、session、focus、计数和下一步建议。
`engram_stats` 返回不含内容的命名空间统计（episode backlog、事实/图谱/冲突计数），适合健康检查。
MCP 的 `engram_remember` 支持 `scope="long"|"auto"|"working"`：长期事实进长期记忆，当前任务临时状态
用 `working`。`engram_close_session` 用于线程结束或切换任务时整理该 session：补摘要、清 working memory、持久化。
用户要看哪些 Codex / Claude Code / app 会话写过记忆时，agent 可调用 `engram_list_sessions`；
它只返回 session id、时间和计数，不返回记忆正文。
用户要纠错或删除单条记忆时，agent 可先用 `engram_list_facts` 找到 fact id，再调用
`engram_update_fact` 做精确修改，或预览删除影响后显式确认 `engram_delete_fact`；删除会同时清掉该 fact
的原始来源与同源派生事实，但仍不会清空整个命名空间。
用户要导出自己的记忆时，agent 可调用 `engram_export(response_format="json")`；默认是安全导出
（非敏感 facts + graph），用户明确要完整私有导出时再传 `include_sensitive=true`。
用户要“以后多关注某类信息 / 少召回某类信息”时，agent 可用 `engram_get_focus` 查看当前策略，再用
`engram_set_focus` 更新关注或屏蔽主题。
MCP 还只向模型暴露安全的分身工具：`engram_get_twin_contract`、
`engram_list_capabilities`、`engram_authorize_twin_action`和 `engram_record_twin_action`。
它不暴露合同修改、grant/revoke 或 owner confirmation，也不返回 credential reference。

### JS/TS SDK + OpenAI 兼容（改一个 URL，你现有的 OpenAI 代码就有了记忆）

```ts
import { EngramClient } from 'engram-memory'                  // npm i engram-memory
const engram = new EngramClient({ baseUrl: 'http://42.193.220.197:8456', apiKey: 'my-app' })
await engram.agentStatus({ sessionId: 'app:my-product:conversation-123' }) // 不含正文的接线检查
await engram.remember('我在字节做后端，最喜欢周杰伦。')
const { context } = await engram.recall('我喜欢哪个歌手？')
await engram.closeSession('app:my-product:conversation-123')
const report = await engram.sessionReport('app:my-product:conversation-123') // 这一轮保存了什么

// 直接替换 OpenAI 的 base_url 即可（官方 openai SDK 也行）：
const out = await engram.chat.completions.create({ model: 'engram', messages: [
  { role: 'user', content: '提醒我一下我最喜欢的歌手' } ] })
```

OpenAI 兼容聊天也支持 Engram 扩展：`memory: { session_id: "codex:repo:thread", scope: "auto" }`
用于把轮次归到某个 agent 线程，`memory: { as_of: <epoch 秒> }` 用于查询某个历史时刻的记忆视图，
`memory: { redact_sensitive: true }` 用于在注入上下文时隐藏敏感事实；redacted 上下文只包含非敏感
结构化事实，不包含画像、摘要或原始片段。

数据导出也遵循同一语义：`/v1/export?include_sensitive=false` 只返回非敏感 facts 及其 graph，不包含画像、
摘要或原始对话。
TypeScript SDK 的 `engram.export()` 默认走这个安全导出；只有用户明确要完整私有导出时再传
`{ includeSensitive: true }`。
如果要做 C 端“我的记忆”管理页，SDK 也支持分页查看：
`engram.memories({ factsLimit, factsOffset, episodesLimit, status, query, includeSensitive })`。
独立关系图接口默认也是安全视图；只有明确要完整私有图谱时才传 `/v1/graph?include_sensitive=true`。

### 自部署（数据完全在你自己机器上）

```bash
cp deploy/.env.example deploy/.env              # 把示例 key 换成强随机密钥
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
curl -fsS http://127.0.0.1:8000/ready
```

标准容器以非 root 运行、根文件系统只读、只把 `/data` 作为持久卷、仅绑定本机端口且不默认开放访问。
TLS 网关、备份恢复、升级回滚和密钥轮换见 [`deploy/README.md`](deploy/README.md)。`GET /health` 用于
liveness/排错，`GET /ready` 在鉴权或存储未就绪时返回 503；两者都不暴露 key、路径或用户内容。

直接使用 Python 部署时，安装 `engram-memory[serve]`，设置
`ENGRAM_API_KEYS="tenant-a:<强随机密钥>"` 后运行 Uvicorn。`ENGRAM_OPEN=1` 仅用于显式本地开发。

批量导入见 [`examples/batch_import.py`](examples/batch_import.py)，完整接口文档见 [`API.md`](API.md)。

## 复现基准

```bash
# 1. 零依赖冒烟测试：quickstart + 离线 harness + 证据日志校验
python scripts/check_zero_setup.py

# 可选：安装测试依赖后跑完整单元测试
pytest

# 2. 在真实干扰集上测检索召回（不需要 LLM）
python eval/longmemeval.py --mode recall --data s --limit 500

# 3. 用官方判分跑完整 QA 基准（需要模型访问；provider 配置见 RESULTS.md）—— 头条 engram_lean + 全文基线
python eval/bench.py --data s --limit 500 --systems engram_lean,full_context \
    --answerer volcano:doubao-seed-2-0-pro-260215 --judge volcano:deepseek-v3-2-251201 \
    --extractor volcano:doubao-seed-1-6-flash-250615 --reasoning --persona \
    --chunks 2 --topk 15 --extract-k 8 --summ-k 28 --n-summaries 28
```

头条数字的**每题原始日志**见 [`RESULTS.md`](RESULTS.md)，含模型预测、标准答案、判分结果、token 数与延迟。
**复现不出我们公布的数字，就是 bug —— 请提 issue。**

## 许可证

Engram 采用**双授权**，按你的用途任选其一：

- **开源 —— [GNU AGPL-3.0](LICENSE)。** 可自由使用、研究、修改、自部署。注意 AGPL 第 13 条：若你以**修改后
  的 Engram 对外提供网络服务**，须按 AGPL 向服务使用者公开该服务的完整源码。内部使用、科研、教学不受影响。
- **商业 —— 单独的付费授权。** 若要把 Engram 用于**闭源/专有**产品，或以 **SaaS / 托管**形式提供、且不愿承担
  AGPL 的源码公开义务，则需获得商业授权。详见 **[`COMMERCIAL-LICENSE.md`](COMMERCIAL-LICENSE.md)**。

一句话：**开源免费；不遵守 AGPL 的商业使用，需要授权。**
