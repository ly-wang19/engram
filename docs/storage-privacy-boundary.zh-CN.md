# Engram 存储与隐私边界

这份文档说明本地个人记忆落盘时，Engram 能保证什么、不能保证什么。它是工程边界说明，
不是“已通过某项合规认证”或“任何介质上都可彻底擦除”的承诺。

## 1. 两层存储不是同一个真相源

| 层 | 角色 | 默认保护 | 删除语义 |
| --- | --- | --- | --- |
| `store.sqlite3` | canonical snapshot；保存 Episode、Fact、图、工作记忆和私有状态 | namespace 目录 0700；DB/manifest/lock 0600；SQLite `secure_delete=ON` | canonical DB 中事务删除，并在重开后校验对象不可达 |
| LanceDB | 可选的派生向量索引；用于向量检索 | 每个实际 Lance 根目录强制 0700；owner marker 0600 | 从 live table 逻辑删除；不等于历史 fragment 的物理擦除 |

SQLite 是可移植、可恢复的 canonical store。LanceDB 是可以从 canonical snapshot 重建的索引层，
不要把“Lance 查询不到了”解释成底层所有旧字节都已经消失。

## 2. LanceDB 路径与命名空间绑定

使用 `Memory.open(canonical_path, config=Config(storage="lancedb", data_path=base))` 时，`data_path`
被解释为私有的**基目录**。Engram 根据 canonical snapshot 的规范化路径派生稳定子目录：

```text
<base>/                                      # 0700
├── .engram-lancedb.json                  # base ownership marker，0600
└── namespaces/store-<sha256(canonical-path)>/  # 0700
    ├── .engram-lancedb.json              # canonical namespace binding，0600
    └── ...                               # LanceDB 自有表与 fragments
```

因此，同一个 canonical snapshot 重开会命中同一索引；两个不同 snapshot 即使配置了同一个 base，
也不会共用 Lance 表。直接构造 `LanceDBVectorStore(path)` 时，`path` 本身就是调用者声明的单一
namespace，不应被不同个人、租户或 snapshot 复用。

要做持久个人记忆，应从 `Memory.open(canonical_path, config=...)` 开始。单独调用
`Memory(config=Config(storage="lancedb", data_path=...))` 没有 canonical snapshot 可供绑定，此时该路径只能
被视为调用者自行隔离的 standalone namespace，不要在多个 Memory 实例间共用。

安全规则：

1. 显式 base 和最终 Lance 根都必须是当前用户拥有的真实目录；symlink、普通文件和运行时换
   inode 都拒绝。
2. base 和根目录每次打开都收紧为 0700；owner marker 必须是当前用户拥有的 single-link regular file，
   并收紧为 0600。
3. table name 只接受有限的字母、数字、下划线和连字符集合。
4. 非空但没有 owner marker 的旧 base/根目录不会被静默接管。这避免把未知/其他 snapshot 的向量误混入
   当前个人记忆。
5. 旧 Lance 目录升级时，使用一个全新 `data_path`，再用 `Memory.open` 从 canonical SQLite
   重建索引。若旧部署没有 canonical snapshot，先在旧版本中做受控导出，再导入新 namespace；
   不要通过手写 marker 绕过校验。
6. canonical snapshot 的规范路径参与 namespace 派生；移动/重命名 snapshot 后会在新 namespace
   从 SQLite 重建。验证新索引后应按保留策略退役旧 namespace，否则旧 fragments 仍会占用介质。
7. 如果 canonical snapshot 为空/已丢失，但同路径派生的 Lance namespace 仍有向量，`Memory.open`
   会 fail closed，不把派生索引当作权威记忆复活。保留/导出旧数据必须先恢复 canonical snapshot；否则
   更换全新 base，并按删除边界处置孤儿索引。

owner marker 绑定解决的是“目录/namespace 误复用”，不是多进程事务协调。canonical SQLite 仍是
并发代际与恢复的权威来源。

## 3. 删除与擦除的准确含义

Engram 的 verified erasure receipt 只能证明其声明范围内的**当前 canonical store**和当前可查询
索引中不再有目标对象。对于 LanceDB，`table.delete(...)` 是逻辑删除；下列副本不在该证明范围：

- 旧 Lance fragments、尚未回收的文件页或临时文件；
- APFS/虚拟机/云盘 snapshot、Time Machine 或其他备份；
- Dropbox、iCloud Drive、OneDrive 等同步服务的版本历史；
- SSD 控制器的 flash translation layer、磨损均衡和 over-provisioned blocks；
- 已经导出、复制、发送给模型供应商或写入第三方日志的数据。

所以不能承诺“物理介质逐字节擦除”。高敏感个人记忆应通过**加密介质 + 密钥销毁 + 明确的备份/
同步保留策略**控制残留风险；需要处置整机时，按设备和组织的介质销毁流程执行。

## 4. 静态加密边界

0700/0600 是本机访问控制，不是内容加密。Engram 当前不在应用层为 SQLite 或 LanceDB 自动加密；
部署方必须把 canonical store、Lance base、备份和交换/休眠文件放在可信的加密边界内：

- macOS：开启 FileVault；高隔离场景使用单独的加密 APFS volume/disk image。
- Linux：使用 LUKS/dm-crypt 或等效的加密卷，并妥善管理解锁密钥。
- 云主机/容器：为持久卷和 snapshot 开启平台静态加密；限制 IAM 与备份读取权限。
- 不把个人记忆放进默认云同步目录；若业务需要同步，先确认端到端加密、保留期和删除传播语义。

磁盘已解锁且进程正在运行时，拥有当前用户权限或 root/管理员权限的主体仍可能读取明文。凭据、
API key、私钥和恢复码应保存在系统 Keychain/KMS/secret manager；记忆中只保存不含秘密值的引用。

## 5. 个人部署检查单

- canonical snapshot 与 Lance base 都位于加密卷；未被公开同步或自动上传。
- `data_path` 是私有基目录，不与另一个人、租户或环境共用实际 namespace。
- 备份有加密、最小权限、保留期限和可验证删除流程。
- 导出、模型调用、日志和可观测性系统均做数据最小化与脱敏。
- 删除后按风险检查 canonical store、当前索引、备份和外部副本；不把逻辑删除报告成物理擦除。

不安装 LanceDB 也能运行路径安全测试：

```bash
pytest -q tests/test_lancedb_security.py
```

安装可选后端后，再运行真实持久化/检索 parity：

```bash
pip install -e '.[lancedb]'
pytest -q tests/test_lancedb_store.py tests/test_lancedb_security.py
```
