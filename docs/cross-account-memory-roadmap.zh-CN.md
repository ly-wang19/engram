# 跨账号个人记忆总线 Roadmap

最后更新：2026-08-12
状态：阶段 1 已落地（本仓当前分支），阶段 0 等待运维执行，阶段 2 起按序推进。

## 目标与定位

让 Engram 成为「记忆跟人走」的个人记忆总线：**换厂商账号、换 AI 客户端、换设备、换服务器，
记忆都还在**。身份锚点是「服务器 + API key + namespace」，与任何厂商账号零耦合——所以"跨账号"
不是要新增的能力，而是架构已经成立的性质；本 roadmap 要补的是让这个性质在真实世界可用的工程链条。

判断每一步是否值得做的标准（对齐 CLAUDE.md 战略）：是否消除一个"记忆会丢/会断/会泄漏"的场景。

## 现状基线（2026-08-12 审计结论）

已经扎实的（不要重做）：

- 0.1.0 商业版全部落地：严格 key 鉴权（一租户多 key 轮换）、SHA-256 摘要命名空间、JSONL+manifest
  崩溃安全持久化、非 root 容器 + systemd、`/health` `/ready` 分离、发布门禁 CI。
- 跨 agent 接入层：`engram-agent-setup / -doctor / -bootstrap` 三个 CLI 覆盖 Codex / Claude Code /
  Cursor；MCP stdio + streamable HTTP；OpenAI 兼容代理；TS SDK；跨进程 manifest 指纹重载。
- **本次新增（阶段 1）**：export→import 原生回路（`format="engram"`，幂等、保 id/双时间戳/
  supersedes 链、目标端重嵌入）；MCP HTTP Bearer 门（`--http-token`，非回环无 token 拒绝启动）；
  `ENGRAM_STORAGE` 后端选择；`/v1/import` 坏 payload 返回 400；import CLI 与服务端目录统一；
  `/v1/stats` 身份规范化。验收：`tests/test_cross_account_portability.py`、
  `tests/test_server_import_export.py`、`tests/test_mcp_http_auth.py`（22 个新测试，全量回归绿）。

主要欠账（按对目标场景的阻塞度排序）：

1. 用户自己的线上实例是 6 月的 demo 档（内存后端、Hashing embedder、公网明文 HTTP、弱 key）。
2. key 生命周期：无自助创建/轮换/吊销 API，无 per-key 审计；改 key 要改 env 重启。
3. 无限流、无 CORS（全部外推反代）；`/health` 无鉴权暴露运营信息。
4. LanceDB 后端 ~40%（where 过滤全表扫、无 DocStore/GraphStore、与 JSONL 双写）；
   每次写全量重写 JSONL（写放大 O(全部记忆)）。
5. 单进程单 worker；热租户 LRU 64。

---

## 阶段 0 —— 把自己的实例升级到 0.1.0（运维，本周可完成）

**为什么第一**：个人记忆正在公网明文 HTTP 上传输，key 是可猜的 `my-app`，后端是内存档——
这不是功能缺口，是正在发生的风险。所有后续阶段都以一个可信实例为前提。

行动清单：

1. 用 `deploy/docker-compose.yml` 部署 0.1.0：`ENGRAM_API_KEYS=me:<32+随机字符>`、持久卷、
   `ENGRAM_EMBEDDER=bge-small`（或 bge-m3）、可选 `ENGRAM_LLM` 开启 LLM 抽取。
2. TLS 反代（Caddy/Nginx，自动续期），只转发受信 Host；按 `deploy/README.md` 网关清单配限流。
3. 旧数据迁移：旧实例 `GET /v1/export?include_sensitive=true` → 新实例
   `POST /v1/import format=engram`（阶段 1 的成果使这一步成为可能；两端 embedder 不同也可以）。
4. 本机 MCP 配置切到新地址+新 key（`engram-agent-setup --install-mcp-json --doctor ...`）。
5. 旧实例下线或封端口。

**验收**：`/ready` 200；写入→容器重启→召回保留；`engram-agent-doctor --api-url https://... --api-key ...`
全绿；旧实例数据在新实例可召回；`engram_agent_status` 显示 storage/embedder 为新配置。

**决策点（唯一需要 owner 拍板）**：服务器驻留——继续用现有云主机（跨设备，但要信任那台机器），
还是退回本机/家庭服务器 + 内网穿透（隐私最优，跨设备体验差一档）。默认建议：现有云主机 + TLS +
强 key，敏感 facts 用 `sensitive` 标记走默认脱敏导出。

## 阶段 1 —— 可迁移性（已完成，本分支）

见「现状基线-本次新增」。遗留小项（不阻塞）：

- TS SDK 补 `importExport` 便捷方法（服务端已支持，SDK 里 `import` 端点已在，只差文档示例）。
- 控制台 Privacy 页加"导入"入口（目前只有导出下载）。

## 阶段 2 —— 跨客户端一致体验（1–2 周）

存储层是通用的，行为层不是：MCP 只保证工具存在，不保证 agent 会调。这一阶段把"每个客户端都会
正确地 recall/remember"变成被验证的事实。

1. **Codex 接入解冻**：`~/.ai-shared/collab.md` 第 5 条的"暂缓"解除——阶段 0 完成后执行
   `engram-agent-setup --install-codex --doctor --api-url ... --api-key ...`，并用
   `--install-policy` 写入 AGENTS.md 记忆策略块。
2. **claude.ai 网页/移动端**（真正的"换账号也能用"）：`python -m engram.mcp --http --http-token ...`
   挂在 TLS 反代后，作为 remote MCP connector 添加到 claude.ai 账号。换账号 = 在新账号加一次
   connector（分钟级，一次性）。
3. **Cursor / 其它 MCP 客户端**：同一 `.mcp.json` 配方。
4. **召回策略统一**：各客户端的 policy 文案统一维护在 `docs/agent-adapters.md`，变更走 bootstrap
   的 managed block，避免各处漂移。

**验收**：`engram-agent-doctor` 对每个客户端跑通远程生命周期（status→remember→close→report→recall）；
一条在 Codex 写入的决策，能在 Claude Code 与 claude.ai 网页端被 recall 命中（跨 agent handoff 冒烟，
`examples/cross_agent_handoff.py --base https://...`）。

## 阶段 3 —— key 生命周期与安全深化（2–4 周）

把"key 即身份"从裸配置升级为可管理的凭据体系（仍是单节点自托管边界，不做企业 SSO/RBAC）：

1. key 管理 API + 控制台页：同租户多 key 的创建/吊销（写回 env 或独立 keys 文件均可，落盘方案
   先出一页设计再动手）；不再要求重启。
2. per-key 审计：写路径在 episode/fact provenance 侧记录 key 指纹（不是 key 明文），回答
   "哪台设备/哪个 agent 写了这条"。
3. `/health` 信息收敛：无鉴权时只报 `ok/ready/version`，运营细节移入鉴权后的 `/v1/stats`。
4. 应用层限流（简单令牌桶，按租户），不再完全依赖网关；CORS 白名单可选项。

**验收**：吊销一个 key 后旧连接立即 401 且其它 key 不受影响；审计字段在 session_report 可见；
未鉴权 `/health` 响应不含 embedder/租户数。

## 阶段 4 —— 规模与后端（与算法路线并行，按 harness 证据推进）

1. **增量持久化**：JSONL 追加 + 定期 compaction，替代每写全量重写（先用 harness/压测量化写放大，
   再动手——Measure Before Optimizing）。
2. **LanceDB 补全**：where 过滤下推（先按 user_id 分表或 Lance filter 表达式）、大表 ANN 索引；
   DocStore/GraphStore 是否迁移按测量决定。
3. multi-worker 前置条件梳理（文件锁已可跨进程，线程锁失效面要清点）。

**验收**：10 万 facts 规模下写延迟与 p95 recall 延迟对比基线成表（accuracy + tokens + latency 三联，
遵守 Bet D），否则不合并。

## 阶段 5 —— 产品面（Path-A 开发者基础设施定位的延伸，按需启动)

- 控制台：导入 UI、key 管理 UI、迁移向导（导出→导入一条龙）。
- 托管多用户注册/开通流（若决定做 hosted 服务，需要真正的用户体系——独立规格，不混入本 roadmap）。

---

## 与既有机制的关系

- **Claude Code auto-memory（MEMORY.md）与 Engram 并行**：auto-memory 绑定机器+项目路径，作为
  本机缓存继续用；跨机器/跨客户端的持久事实沉到 Engram。阶段 2 完成后可评估把 auto-memory 的
  高价值条目定期 `engram_remember` 化（脚本化，不手抄）。
- **公开数字纪律**：本 roadmap 全部为工程交付，不产生任何算法性能主张；LongMemEval 相关数字
  仍以 `RESULTS.md` + 已提交 JSONL 为准。
