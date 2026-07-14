# 快速验收：Engram 0.1.0 商业交付版

所有命令从仓库根目录执行。验收结果应记录到 `results/commercial_release_0_1_0_validation.jsonl`。

## 1. 规格前置检查

```bash
.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks
rg -n "NEEDS CLARIFICATION|ACTION REQUIRED|REMOVE IF UNUSED|\[FEATURE\]|\[DATE\]" \
  specs/003-commercial-release/spec.md \
  specs/003-commercial-release/plan.md \
  specs/003-commercial-release/research.md \
  specs/003-commercial-release/data-model.md \
  specs/003-commercial-release/contracts
```

预期：前置检查指向 `specs/003-commercial-release`，占位符扫描无输出。

## 2. 零配置与完整 Python 测试

```bash
python scripts/check_zero_setup.py
pytest
```

预期：两项均退出 0；不需要模型 API key 或外部服务。

## 3. 发布门禁与 Python 包

```bash
python scripts/check_release.py
python -m build
python -m twine check dist/*
```

预期：版本、许可、文档、证据入口和包元数据全部通过；wheel 与 sdist 可安装检查通过。

## 4. TypeScript SDK 与管理界面

```bash
npm --prefix clients/typescript ci
npm --prefix clients/typescript run typecheck
npm --prefix clients/typescript test
npm --prefix clients/typescript run build

corepack pnpm --dir frontend install --frozen-lockfile
corepack pnpm --dir frontend run build
```

预期：类型检查、测试和两个构建均退出 0，管理界面输出到 `frontend/dist/`。

## 5. 安全与租户隔离定向测试

```bash
pytest -q \
  tests/test_service_paths.py \
  tests/test_server.py \
  tests/test_release.py
```

预期：目录穿越、碰撞、跨租户、鉴权误配置、请求上限、健康/就绪和版本漂移测试全部通过。

## 6. 标准容器部署与重启恢复

```bash
cp deploy/.env.example deploy/.env
# 将示例 key 替换为本机临时强密钥后：
docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d --build
docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps

curl -fsS http://127.0.0.1:8000/ready
curl -fsS -X POST http://127.0.0.1:8000/v1/remember \
  -H "Authorization: Bearer $ENGRAM_TEST_KEY" \
  -H "Content-Type: application/json" \
  -d '{"content":"release canary 2026-07-14","session_id":"release-smoke"}'

docker compose --env-file deploy/.env -f deploy/docker-compose.yml restart
curl -fsS -X POST http://127.0.0.1:8000/v1/recall \
  -H "Authorization: Bearer $ENGRAM_TEST_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"release canary","n_chunks":3}'
```

预期：容器 healthy；重启后 recall 仍返回 canary 相关证据。验收完成后执行：

```bash
docker compose --env-file deploy/.env -f deploy/docker-compose.yml down -v
rm deploy/.env
```

## 7. 最终一致性

```bash
git diff --check
git status --short
```

预期：无空白错误；只包含本功能文件和明确纳入的发布证据，不包含历史未跟踪实验日志。
