# 嵌入如我 · 第 0 步验收记录（2026-10-31）

Claude Code。方案见如我开发目录 `docs/design/engram-personal-memory-module-plan-20261031.md`，正本在 Claude Doc「如我 AI 代码地图」→「Engram 记忆模块方案」。本人 10-31 对 7 项拍板“按建议开工”。

## 工作区

- 隔离 worktree：`.claude/worktrees/ruwo-embed-20261031`，分支 `claude/ruwo-embed-20261031`，基于 HEAD 267a850b。
- 主工作区开工时的全部未提交改动（44 个已跟踪文件、108 个未跟踪文件，不含 results/）已原样带入，并暂存为基线（`git add -A`，未提交）。所以 `git diff` 显示的就是本轮改动。
- 本人 10-31 同意“提交并推送跑 CI”：分支已推送（不开 PR，不合 main），基线快照单独一个提交（2841664f），本轮改动在其后。未部署，没有动主工作区、8456/8443 实例和本机 8766 服务。

## 关口 1

| 条件 | 结果 | 证据 |
|---|---|---|
| 中文问题能搜到 | 通过 | `tests/test_cjk_tokenize.py`（5 项）；`test_audit_no_embedder_blind_on_hashing_chinese_store`（2 字词“熔断”命中）；`tests/test_embedded.py` 经 HTTP 用 2 字词“合同”召回命中 |
| 单人库内存低于 300MB | 按当前规模通过 | 按本机真实规模合成：2280 个 Codex 会话，每个 24k 字窗口、2k 字摘要，另有 1 万条结论，盘上 141MB。冷启动打开后进程 270–286MB（含约 40MB Python 本体），召回加追加写的峰值 289–293MB。改动前同一数据为 368MB |
| Windows CI 全绿 | 通过 | [run 41177893191](https://github.com/ly-wang19/engram/actions/runs/41177893191)：windows-latest 846 通过、51 跳过（POSIX 专属）；Linux 3.10/3.12、前端、容器全部通过 |
| Engram 全量测试通过 | 通过 | 离线完整测试 903 通过、1 跳过（开工基线 878 通过）；zero-setup、release check 通过；ruff 与基线相同；前端 tsc 和两种构建通过 |

## 改了什么（按方案的七处硬伤）

1. **中文分词**
   - 中日韩连续字切成重叠二元组。`content_term` 取代 evidence、aggregate、conflict 里的 `len>2` 过滤。
   - hashing 身份改为 `hashing-cjk2`。旧库打开时只重嵌含中文的条目，其他嵌入器不一致仍拒绝。
   - blind 诊断改为只对 tokenizer 仍丢掉的文字（俄文、泰文等）报警。
2. **同一会话多份**：agent_session 会话变长时原地替换旧 episode，id 不变；返回值新增 `grown` 计数。
3. **写盘放大**
   - episodes.jsonl 改为追加写，manifest 记录 `sizes.episodes`，同 id 后一行生效。
   - 删除或失效行超过 1/4 时才重写；备份只覆盖要替换的文件。
   - 中断的追加不会被读到，下次保存时先截掉。
4. **召回时全量拼接**：改为逐条累计估算。
5. **摘要送全文**：截成 24000 字窗口（头 35%、尾 65%）。新增 `agent_session_max_chars`，嵌入入口默认 24000。
6. **Windows**
   - 新增 `engram/filelock.py`，persist、service、jobs、watch 四处锁统一走它。
   - 目录 fsync 和 fchmod 在 Windows 上跳过；`os.replace` 遇到 PermissionError 时重试。
   - POSIX 专属的测试已标记跳过。
7. **watch 排除如我线程**
   - 读会话头部记录的 cwd，匹配 `/conversations/(o2o|group|mail)-<20hex>` 的跳过；可用 `ENGRAM_WATCH_EXCLUDE_CWD` 改写或关闭。
   - 支持 `CODEX_HOME`；session_hook 用同一规则。
   - 本机只读核对：1158 个会话里排除了 85 个，全部来自如我的三个组织工作区。

另外三项：

- **codex 引擎**：`llm/codex_llm.py`。由宿主 runner 执行，全进程一次一个回合，每日上限默认 60，遇到限流冷却 15 分钟，宿主忙时等待；任何拒绝都抛 `CodexUnavailable`，不返回空串。
- **嵌入模式**
  - 服务端：`ENGRAM_EMBEDDED=1` 时只允许同源 frame；`ENGRAM_UI_DIST`；`ENGRAM_NO_DOTENV`。
  - 前端：`VITE_BASE`、`VITE_API_BASE`、`VITE_EMBEDDED`。嵌入版不显示登录页、密钥框和退出按钮。
- **启动器**：`python -m engram.embedded`。端口 0 加 announce 文件；生成 0600 密钥，单命名空间 `me`，忽略 `ENGRAM_OPEN`；随父进程退出。

## Windows CI 发现并修掉的问题

第一次跑 Windows 时 838 通过、8 失败，修完后全绿：

- **清空记忆在 Windows 上做不了。** `forget` 在持有命名空间里 `.service.lock` 的同时整目录删除，Windows 拒绝删除被打开的文件（WinError 32）。改为先清空目录，释放锁后再删锁文件和目录。
- **等待时间设为 0 仍然会等。** watch 的 `quiet_seconds=0` 本应不等待，但 Windows 上刚写的文件 mtime 可能比 `time.time()` 晚几毫秒，被当成“仍在写”。0 现在表示不等。
- **测试没考虑 Windows。** zero-setup 输出的路径统一用 `/`；密钥文件测试改为比较 TOML 转义后的路径；forget 安全测试的替身接受关键字参数。
- **CI 配置的两处疏漏。** 一是 `vite.config.ts` 读 `process.env` 时没有 Node 类型，本地能过只因为借用了主仓库的 node_modules，现改为局部声明；二是 Windows 任务漏装了 mcp 依赖组。

## 已知边界

- **内存随会话数增长。** 每个会话约 56KB（24k 字窗口、摘要、向量）。按每天约 30 个会话算，一个月约增加 50MB，几个月后会超过 300MB。长期需要冷热分层：较早会话的正文不常驻内存，按需从 episodes.jsonl 读取。这一项尚未实现，是第 1 步之外的下一项。
- **召回速度。** 在这个规模、机器负载 11–19 的情况下，每次查询 0.5–1.3 秒，原因是纯 Python 逐条比对向量。这是既有行为，本轮共享浮点没有让它变慢（同进程对照 1.18s 对 1.31s）。
- **24k 窗口会丢掉会话中段。** 证据跳转只能看到窗口里的内容，完整原文仍在 Codex 会话文件中。
- **旧版读不了新的备份标记。** 新格式的 ready.json 旧版不认识，回退到旧版前须先由本版恢复未完成的保存。
- **Codex runner 还没写。** 它要在如我侧（第 1 步）实现，本轮只用替身 runner 测试，没有调用真实 Codex。

## 复现

```bash
python3 -m pytest -o addopts="" -q
```

内存测量（合成数据，不含任何真实对话）：

```bash
PYTHONPATH=. python3 docs/validation/ruwo-embed-20261031/build.py /tmp/engram-gate1
PYTHONPATH=. python3 docs/validation/ruwo-embed-20261031/measure.py /tmp/engram-gate1
```
