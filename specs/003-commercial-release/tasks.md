# Tasks: Engram 0.1.0 商业交付版

**Input**: `specs/003-commercial-release/` 下的 `spec.md`、`plan.md`、`research.md`、`data-model.md`、`contracts/` 和 `quickstart.md`

**Tests**: 本功能要求 TDD；安全、隔离、持久化和发布门禁必须先有失败测试，再实现。

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: 不修改同一文件且不依赖未完成任务，可并行。
- **[Story]**: 对应 `spec.md` 的 US1/US2/US3。

## Phase 1: Setup

- [x] T001 创建并校验 `specs/003-commercial-release/` 全套 Spec Kit 文档与契约
- [x] T002 更新 `.specify/feature.json`、`AGENTS.md` 和 `CLAUDE.md` 的当前计划指针
- [x] T003 闭环 `specs/002-memory-reference-radar/spec.md` 与 `specs/002-memory-reference-radar/tasks.md` 的真实完成状态
- [x] T004 [P] 补齐 `specs/002-memory-reference-radar/research.md` 中 benchmark/radar 来源的证据与许可状态
- [x] T005 [P] 校验 `.gitignore` 并创建适用于 Python/Node/容器构建上下文的 `.dockerignore`

---

## Phase 2: Foundational

- [x] T006 在 `tests/test_release.py` 创建 0.1.0 版本、许可、关键文档和发布文件存在性失败测试
- [x] T007 在 `tests/test_service_paths.py` 创建危险命名空间、规范化碰撞、旧目录兼容和删除边界失败测试
- [x] T008 在 `tests/test_server.py` 创建鉴权配置、liveness/readiness、请求上限和安全响应头失败测试
- [x] T009 在 `engram/__init__.py` 暴露单一公开 `__version__`，并为服务和发布检查提供版本源
- [x] T010 在 `engram/server/app.py` 增加可复用的配置错误与健康状态基础结构

**Checkpoint**: 发布和安全需求已有自动化失败测试，用户故事实现可开始。

---

## Phase 3: User Story 1 - 安全地部署并持续运行 (Priority: P1)

**Goal**: 默认关闭开放访问，区分存活/就绪，限制请求，提供可重启持久化的标准容器部署。

**Independent Test**: 按 `specs/003-commercial-release/quickstart.md` 启动受保护容器，写入、重启、召回；去掉鉴权后 `/ready` 返回 503。

- [x] T011 [US1] 在 `engram/server/app.py` 严格解析 `ENGRAM_API_KEYS` 并支持一租户多密钥轮换
- [x] T012 [US1] 在 `engram/server/app.py` 使用常量时间密钥比较并保持无鉴权默认失败关闭
- [x] T013 [US1] 在 `engram/server/app.py` 实现 `/health` liveness 与 `/ready` readiness 契约
- [x] T014 [US1] 在 `engram/server/app.py` 实现 `ENGRAM_MAX_REQUEST_BYTES` 和请求字段/分页边界
- [x] T015 [US1] 在 `engram/server/app.py` 为 API/UI 增加 no-store、nosniff、frame、referrer 和内容安全响应头
- [x] T016 [P] [US1] 创建非 root 多阶段 `Dockerfile` 并包含构建后的 `frontend/dist/`
- [x] T017 [P] [US1] 创建 `deploy/docker-compose.yml` 与 `deploy/.env.example` 的安全默认部署
- [x] T018 [US1] 更新 `deploy/engram.service`，移除机器专用日志路径并增加更严格的 systemd 防护
- [x] T019 [US1] 在 `tests/test_server.py` 完成受保护写入、未鉴权拒绝、请求拒绝发生在写入前的回归测试

**Checkpoint**: 标准部署默认受保护，健康状态可用于编排，数据卷可跨重启恢复。

---

## Phase 4: User Story 2 - 隔离且可审计地接入应用 (Priority: P2)

**Goal**: 任意逻辑租户 ID 都映射到唯一安全目录，旧合法目录继续工作，删除不越界。

**Independent Test**: 对 `a/b`、`ab`、`.`、`..`、绝对路径、中文和超长 ID 执行写入/读取/删除，所有路径都留在临时数据根目录且互不影响。

- [x] T020 [US2] 在 `engram/service.py` 实现“安全前缀 + SHA-256 摘要”的确定性命名空间目录
- [x] T021 [US2] 在 `engram/service.py` 对真实路径执行数据根目录包含性校验
- [x] T022 [US2] 在 `engram/service.py` 实现仅限完全安全旧 ID 的目录与 pickle 回退兼容
- [x] T023 [US2] 在 `engram/service.py` 收紧文件锁和整租户删除目标，禁止删除数据根目录或父目录
- [x] T024 [US2] 在 `tests/test_service_paths.py` 完成碰撞、Unicode、超长 ID、点目录、绝对路径和旧目录迁移回归测试
- [x] T025 [US2] 在 `tests/test_server.py` 增加两租户交叉读取、导出、修改、单条删除和整租户删除隔离测试
- [x] T026 [US2] 在 `API.md` 和 `README.zh-CN.md` 记录稳定租户 ID、多密钥轮换和安全命名空间语义

**Checkpoint**: 命名空间无法路径越界或碰撞，客户数据权利操作保持租户边界。

---

## Phase 5: User Story 3 - 可验证地发布和支持 0.1.0 (Priority: P3)

**Goal**: 所有产物版本一致，发布门禁、CI、中文文档和支持边界完整。

**Independent Test**: 从干净检出运行 `specs/003-commercial-release/quickstart.md`，任一版本漂移或失败检查均阻断发布。

- [x] T027 [US3] 将 `pyproject.toml`、`clients/typescript/package.json`、`clients/typescript/package-lock.json` 和 `frontend/package.json` 统一为 0.1.0 beta
- [x] T028 [US3] 在 `engram/store/persist.py` 与 `engram/server/app.py` 使用统一 0.1.0 版本
- [x] T029 [US3] 在 `scripts/check_release.py` 实现无第三方依赖的版本、许可、文档、证据和发布资产门禁
- [x] T030 [US3] 完成 `tests/test_release.py` 对发布门禁成功与版本漂移失败的测试
- [x] T031 [P] [US3] 创建 `SECURITY.md`，记录漏洞报告、支持版本、秘密处理和生产边界
- [x] T032 [P] [US3] 创建 `CHANGELOG.md`，记录 0.1.0 能力、兼容性、安全修复和已知限制
- [x] T033 [US3] 重写 `deploy/README.md` 为中文优先的部署、备份、恢复、升级、回滚、密钥轮换和网关清单
- [x] T034 [US3] 创建 `docs/commercial-release-0.1.0.zh-CN.md` 阶段交付报告与支持范围
- [x] T035 [US3] 更新 `README.md` 与 `README.zh-CN.md` 的 0.1.0 状态、容器入口和安全默认说明
- [x] T036 [US3] 创建 `.github/workflows/ci.yml`，运行 Python、SDK、前端、发布门禁和容器构建检查
- [x] T037 [US3] 更新 `docs/architecture-optimization-map.zh-CN.md` 的商业交付安全优化台账
- [x] T038 [US3] 更新 `docs/engram-full-architecture-report.zh-CN.md` 的鉴权流、命名空间落盘、部署和发布数据流

**Checkpoint**: 0.1.0 有统一身份、可构建产物、自动门禁、支持边界和中文运维入口。

---

## Phase 6: Polish & Cross-Cutting Validation

- [x] T039 运行 `python scripts/check_zero_setup.py` 并记录结果到 `results/commercial_release_0_1_0_validation.jsonl`
- [x] T040 运行完整 `pytest` 与定向安全测试并记录通过数到 `results/commercial_release_0_1_0_validation.jsonl`
- [x] T041 [P] 运行 TypeScript SDK typecheck/test/build 与前端 build 并记录结果到 `results/commercial_release_0_1_0_validation.jsonl`
- [x] T042 [P] 构建 Python wheel/sdist、运行元数据检查并记录结果到 `results/commercial_release_0_1_0_validation.jsonl`
- [x] T043 构建标准容器并执行鉴权、健康、写入、重启、召回持久化冒烟，记录到 `results/commercial_release_0_1_0_validation.jsonl`
- [x] T044 运行 `python scripts/check_release.py`、占位符扫描、`git diff --check` 和隐私/秘密扫描
- [x] T045 将全部完成任务标记为 `[x]`，把 `specs/003-commercial-release/spec.md` 状态更新为 Completed
- [x] T046 审查最终 diff 只包含本次交付文件，不纳入历史未跟踪 `results/*.jsonl`

---

## Dependencies & Execution Order

- Phase 1 -> Phase 2 -> US1/US2 -> US3 -> Phase 6。
- US1 的鉴权与健康可在路径基础测试后进行；US2 必须在任何容器持久化最终验收前完成。
- T016/T017、T031/T032 可并行；修改 `engram/server/app.py` 与 `engram/service.py` 的任务各自串行。
- T039-T043 可在实现完成后并行启动，但 T045 只能在所有结果通过后执行。

## Parallel Examples

```text
T016 Dockerfile || T017 Compose/env
T031 SECURITY.md || T032 CHANGELOG.md
T041 SDK/frontend build || T042 Python package build
```

## Implementation Strategy

1. 先让安全与发布测试失败，证明测试能抓住现有缺口。
2. 先完成 P1 默认安全部署，再完成 P2 命名空间隔离。
3. P3 只在核心安全回归通过后统一版本和公开状态。
4. 所有门禁通过并留下结果日志后才提交 PR；本次不跑新的 500 题算法实验。
