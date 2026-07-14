# 契约：0.1.0 自托管部署

## 必需配置

| 配置 | 默认 | 生产要求 |
| --- | --- | --- |
| `ENGRAM_DATA_DIR` | `~/.engram/data` | 容器使用持久卷 `/data` |
| `ENGRAM_API_KEYS` | 空 | 必须配置 `tenant:key`，同租户可重复出现以轮换密钥 |
| `ENGRAM_OPEN` | false | 生产禁止启用 |
| `ENGRAM_ALLOW_ANONYMOUS` | false | 生产禁止启用 |
| `ENGRAM_MAX_REQUEST_BYTES` | 2097152 | 根据网关限制调整，必须为正数 |
| `ENGRAM_EMBEDDER` | `hashing` | 可按质量需求换可选后端 |
| `ENGRAM_LLM` | 空 | 可选；为空时规则抽取仍可运行 |

## 鉴权配置语义

```text
ENGRAM_API_KEYS=tenant-a:key-a1,tenant-a:key-a2,tenant-b:key-b1
```

- 同租户多密钥合法，用于先加新密钥、切流、再删旧密钥。
- 空租户、空密钥、缺少分隔符或同一密钥映射不同租户为配置错误。
- 密钥不得出现在健康响应、日志、异常文本或导出数据中。
- 未配置密钥时，只有显式 `ENGRAM_OPEN=1` 才可使用 Bearer 文本作为开发命名空间。

## 健康契约

- `GET /health`: 总是用于 liveness/诊断；200 响应包含 `version`、`auth_mode`、`ready` 和无内容组件状态。
- `GET /ready`: readiness；可用时 200，不可用或配置错误时 503。
- 任一响应不得包含 API key、用户正文或真实数据路径。

## 容器契约

- 最终进程以非 root 用户运行。
- 数据只写入 `/data` 持久卷；容器重建不得丢失数据。
- 镜像不内置密钥，不默认开放匿名访问。
- 管理界面和 HTTP API 同源提供，避免默认跨域暴露。
- TLS、外网速率限制、WAF 和访问日志脱敏由生产反向代理负责。

## 备份与回滚

- 停止写流量后复制整个数据根目录，必须包含每个命名空间的 `manifest.json` 和 JSONL 集合。
- 恢复到新目录后先执行只读/冒烟验证，再切换流量。
- 回滚镜像前保留 0.1.0 二进制和完整备份；旧版本可能不识别新的摘要目录名。
