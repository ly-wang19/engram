# 实施计划：Engram 0.1.0 商业交付版

**Branch**: `codex/commercial-release-v0.1.0` | **Date**: 2026-07-14 | **Spec**: [spec.md](spec.md)

**Input**: `specs/003-commercial-release/spec.md`

## Summary

把 Engram 当前的算法、持久化和多接入面收束成一个可交付的 0.1.0 单节点自托管版本。本计划不改检索算法，
重点修复命名空间落盘安全，强化鉴权配置和请求边界，增加存活/就绪语义、安全响应头、非 root 容器部署、
统一版本与发布门禁，并用完整测试、构建和容器重启冒烟留下发布证据。

## Technical Context

**Language/Version**: Python >=3.10（服务与核心），TypeScript/Node >=18（SDK 与管理界面），容器运行时使用 Python 3.12 与 Node 22 构建阶段。

**Primary Dependencies**: 核心零硬依赖；FastAPI/Uvicorn/Pydantic 为可选 server extra；前端沿用 React/Vite；不新增运行时重依赖。

**Storage**: 现有 JSONL + manifest 目录快照，单租户一个目录；保留可信旧 pickle 显式迁移能力。

**Testing**: pytest、`scripts/check_zero_setup.py`、TypeScript typecheck/node:test/build、前端 build、Python wheel/sdist 检查、Docker Compose 重启持久化冒烟。

**Target Platform**: Linux 单节点自托管服务器；macOS/Linux 开发环境；HTTP/MCP/SDK 客户端。

**Project Type**: Python library + web service + MCP server + TypeScript SDK + React management console。

**Performance Goals**: 不改变现有读写算法目标；请求上限检查在进入记忆处理前完成；健康检查不加载租户正文。

**Constraints**: 核心保持零硬依赖；开放模式必须显式；数据根目录不得被命名空间逃逸；旧合法目录可继续读取；不修改或重述公开算法数字。

**Scale/Scope**: 0.1.0 支持单节点、多租户、中小规模自托管；不包含多区域 HA、云计费、SSO/RBAC、监管认证或分布式限流。

## Constitution Check

*GATE: Phase 0 前检查，并在设计后复查。*

- **I. Reproducibility**: PASS。发布检查命令和结果落到仓库；公开算法数字不变且继续由既有 JSONL 支撑。
- **II. Zero-Setup**: PASS。核心不新增依赖，离线 quickstart 与完整测试作为硬门禁。
- **III. Interfaces First**: PASS。安全和部署增强位于服务、配置与部署边界，不把容器依赖引入核心。
- **IV. No Silent Memory Corruption**: PASS。命名空间升级保留旧目录兼容，数据迁移和删除都有隔离测试。
- **V. Measure Before Optimizing**: PASS。本功能不是算法优化，不新增性能领先结论；只报告发布验收事实。
- **VI. Compose, Don't Pick**: PASS。保留事实 + 原始片段 + 双时间图主链，不改检索组成。
- **VII. Honest Public Messaging**: PASS。状态从 alpha 收束为边界明确的 0.1.0 自托管版本，不宣称企业 HA、SOTA 或合规认证。

**Post-design re-check**: PASS。`research.md`、数据模型、部署契约和 quickstart 均保留上述边界。

## Architecture Decisions

1. **安全命名空间目录**：新命名空间使用“可读前缀 + 原始值 SHA-256 摘要”的确定性目录名；绝不把原始命名空间直接拼接到文件路径。
2. **旧目录兼容**：仅当旧目录名与原始租户 ID 完全一致、不是点目录且位于数据根目录下时才回退读取；新写入优先安全目录。
3. **鉴权失败关闭**：`ENGRAM_API_KEYS` 严格解析，一租户可多密钥、一密钥不可多租户；无密钥且未显式开放时，业务未就绪。
4. **健康语义分离**：`/health` 提供无正文存活诊断，`/ready` 在鉴权和数据目录可用时返回成功，否则返回非 2xx。
5. **请求边界**：中间件检查总请求体大小，Pydantic 模型约束常用字段与分页；默认上限可由环境变量向下或向上调整。
6. **容器交付**：多阶段构建管理界面，最终镜像使用非 root 用户、持久卷、只安装服务所需依赖，默认不启用开放模式。
7. **发布门禁**：一个标准库脚本检查版本、许可、文档、包元数据与证据入口；CI 再运行测试、构建和容器检查。

## Project Structure

### Documentation (this feature)

```text
specs/003-commercial-release/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/requirements.md
├── contracts/deployment.md
├── contracts/namespace-storage.md
└── tasks.md
```

### Source Code (repository root)

```text
engram/
├── __init__.py                 # 公开版本
├── service.py                  # 安全命名空间路径与旧目录兼容
└── server/app.py               # 鉴权、请求边界、安全头、健康/就绪
deploy/
├── docker-compose.yml          # 标准自托管入口
├── .env.example                # 安全配置模板
├── README.md                   # 中文优先部署/升级/回滚
└── engram.service              # systemd 备选
scripts/check_release.py        # 无第三方依赖发布门禁
tests/
├── test_server.py              # 鉴权、请求、健康与安全头
├── test_service_paths.py       # 命名空间隔离/兼容
└── test_release.py             # 版本、文件与发布契约
.github/workflows/ci.yml        # Python/SDK/frontend/release 检查
Dockerfile
.dockerignore
SECURITY.md
CHANGELOG.md
docs/commercial-release-0.1.0.zh-CN.md
```

**Structure Decision**: 沿用现有模块边界；安全路径属于 `MemoryService`，HTTP 防护属于 server，交付资产位于根目录与 `deploy/`，不新增独立框架或存储抽象。

## Complexity Tracking

无宪章违规。安全目录名增加摘要是为消除路径穿越与规范化碰撞；保留受限旧目录回退是为了不静默丢失已有数据。

## Delivery Phases

1. **规格与基线**：闭环 002 任务状态，生成 003 规格、计划、任务和验收契约。
2. **安全核心**：先写路径/鉴权/请求边界测试，再实现服务修复。
3. **交付资产**：统一 0.1.0 版本，增加容器、部署、安全、变更和升级文档。
4. **发布门禁**：增加 release checker 与 CI，构建所有产物。
5. **最终验收**：完整 pytest、zero-setup、SDK/frontend、包构建、容器重启持久化冒烟，保存结果并更新架构文档。

## Rollback

- 代码回滚到发布前主分支不会删除任何新安全目录；旧版本只是不认识新目录，因此回滚前必须导出或保留 0.1.0 二进制。
- 标准部署使用版本化镜像标签，回滚时保留同一数据卷并先做备份。
- 任一隔离、持久化、零配置或完整测试失败都阻断合并，不以文档声明替代。
