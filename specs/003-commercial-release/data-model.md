# 数据模型：Engram 0.1.0 商业交付版

## Release

- `version`: 统一语义版本，本阶段固定 `0.1.0`。
- `status`: `beta` / `stable`；本阶段为 `beta`。
- `artifacts`: Python 包、TypeScript SDK、管理界面、容器镜像。
- `commit`: 生成产物的 Git 提交。
- `checks`: 发布门禁结果集合。
- `published_at`: 可选发布日期。
- `support_scope`: 单节点自托管、接口兼容与安全修复边界。

**Validation**: 所有产物版本一致；任一必需检查失败时状态不能进入 released。

## CredentialMapping

- `tenant_id`: 稳定租户身份，保留原始 Unicode 文本用于逻辑身份。
- `api_key`: 不写入日志和响应的秘密。
- `source`: 环境配置或未来秘密管理适配器。
- `active`: 是否有效。

**Relationships**: 一个 `tenant_id` 可以关联多个 `api_key`；一个 `api_key` 只能关联一个 `tenant_id`。

**Validation**: 租户与密钥非空；配置格式完整；冲突密钥使启动/就绪失败。

## NamespaceLocation

- `tenant_id`: 原始租户身份。
- `directory_name`: 安全可读前缀 + 摘要。
- `absolute_path`: 数据根目录下的绝对路径。
- `format`: `jsonl-v1` 或可信旧 `pickle`。
- `legacy_path`: 满足安全旧格式时可选的兼容路径。

**Validation**:

- 同一原始租户始终得到同一 `directory_name`。
- 不同原始租户不得因字符过滤得到同一路径。
- `absolute_path` 的真实路径必须是数据根目录的后代，不能等于父目录或根目录本身。
- `.`、`..`、绝对路径、分隔符、空白和超长 ID 均不能直接成为文件路径。

**State transitions**:

```text
unseen -> secure-directory-created -> active
legacy-valid -> legacy-active -> optional-explicit-migration -> secure-active
active -> tenant-erased
```

删除状态只影响该租户的安全目录及其已验证的旧兼容路径。

## ServiceHealth

- `live`: 进程和应用对象已启动。
- `ready`: 鉴权有效、数据目录可写、核心服务已初始化。
- `auth_mode`: `api_keys`、`open` 或 `disabled`。
- `storage`: 后端类型，不包含路径。
- `version`: 产品版本。
- `diagnostics`: 无正文、无密钥、无文件路径的计数和组件状态。

**Validation**: `auth_mode=disabled` 时 `ready=false`；`/ready` 返回非 2xx。

## DeploymentConfiguration

- `data_dir`: 持久化根目录。
- `api_keys`: CredentialMapping 列表。
- `open_mode`: 仅开发用途的显式开关。
- `allow_anonymous`: 仅在开放模式下生效的显式开关。
- `max_request_bytes`: 请求体上限。
- `embedder` / `llm` / `answerer`: 可选后端选择。
- `hot_limits`: 热用户和热事实边界。

**Validation**: 生产模板不启用开放/匿名；数据目录必须可创建和写入；数值上限必须为正数且有合理最大值。

## ReleaseEvidence

- `check_id`: 稳定检查名。
- `command`: 可复现命令。
- `status`: `passed` / `failed` / `skipped`。
- `environment`: Python/Node/Docker 版本与平台。
- `result`: 高信号摘要，不保存秘密或用户数据。
- `artifact`: 对应日志或构建产物。

**Validation**: 必需检查不得 skipped；失败时发布阻断；算法结果与发布工程结果分开标记。
