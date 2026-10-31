# 六项产品功能发布：2026-10-30

版本：`product-20261030-d64e86214b92`。用户明确授权“全部部署”；目标为既有 lighthouse / engram.service（8456）。**六项功能代码已上线，现有模型凭证失效尚待更新。** 不能把当前 health 中 configured=true 当成模型连接正常。

## 发布范围

- 提炼任务中心、问答证据/纠错、管理员模型配置/探测及来源记录、未决问题、完整体检队列、选择性遗忘全部部署。
- manifest 列出21个后端文件和4个前端文件的 SHA-256。memory.py、config.py、graph_builder.py 来自 manifest.source_head 的稳定版本；不包含另一会话正在改的检索算法、eval、results、MCP/客户端安装改动。
- 必需核心兼容：None导入分流及来源metadata、事实更正display/依据、默认会话结论开关、结论不建实体图。ModelManager同步热实例summarizer.llm，避免模型配置后摘要仍调用旧模型。
- 服务端只新增独立 ENGRAM_ADMIN_KEYS，原模型/业务/数据配置保持。管理员令牌由强随机生成，密钥文件及环境文件权限0600；不在仓库或报告中保留内容。普通namespace不能访问管理员模型接口。

## 部署与验证

- 旧线上核心 + 发布覆盖文件的兼容回归：159 passed / 1 skipped；服务器实际Python 3.12及现有FastAPI/Pydantic/LiteLLM环境的六项隔离API链路通过，无需安装新运行时依赖。
- 停服窗口12.54秒。2675个原数据文件、1,176,018,015字节在停止服务后同步备份，逐文件哈希聚合一致；换代码时原数据未变化。
- 公网health/ready正常，新index/JS/CSS/release.json与manifest完全相符；六个页面、管理员入口、任务失败与重试控件、问题解决关联、体检筛选、来源记录、选择性遗忘确认均已实际回读。
- 浏览器1440px操作和六页390px无横向溢出，运行异常0。真实管理员解锁通过SSH本地转发完成，未在公网HTTP探针中传送管理员令牌。
- 原演示数据始终为77 episodes、117 live facts、35 superseded、77 summaries；私人 engram-personal PID保持2891055。
- 测试仅新增随机隔离namespace与合成记录；验收后全量清理该namespace并核对管理记录归零。见 cleanup-verification.json。

初次兼容检查定位了旧import语义和旧graph-builder不匹配；已按稳定HEAD补齐并通过最终检查。独立客户端watcher的当前测试不适用于未更新的旧远端watcher，未将这些客户端变更扩大到服务端部署范围。

## 当前模型限制与恢复条件

真实模型连通测试失败。服务商返回 authentication_error，明确指向原有DeepSeek凭证无效；provider模型列表请求也拒绝该凭证。没有擅自借用其它组织或私人实例的密钥，没有换模型提供方。

已上线UI能如实显示连接/生成失败；存储、任务管理、问题与体检、纠错及遗忘功能可用。**真实提炼及生成回答需要用户在设置页更新有效模型凭证。** 用户已明确选择“先完成部署，保留当前配置”。本次部署完成交付，模型调用不可用作为已知限制保留，不再等待或反复探测。以后用户明确要求修复模型配置后，仅重验连接、合成提炼和带引用问答三条依赖链，无需重复部署全部代码。

历史原文缺少metadata.source的记录不会自动补齐来源；这部分记录按会话或时间选择遗忘。选择性遗忘的派生清理范围以界面预览为准。

## 备份与回滚

备份：`/home/ubuntu/engram-backups/product-20261030-d64e86214b92`，权限0700。包含旧engram全目录、frontend-dist、.env、完整data。

发布目录：`/home/ubuntu/engram-releases/product-20261030-d64e86214b92`，含冻结包/manifest和验证回执。私人实例不在发布范围。

普通回滚：停止engram.service；把当前engram和frontend/dist移到独立保留目录，再从备份复制旧engram、frontend-dist及.env；启动并检查ready。**保留现有data，不用发布前备份覆盖新写入**。数据恢复另行明确处理。只操作engram.service，不操作engram-personal。

本次没有提交/推送，不产生新的准确率或性能主张。
