# 控制台功能更新：2026-10-29

本次用户明确授权部署，目标是既有 lighthouse / engram.service（8456）。版本指纹见 manifest.json。

## 部署范围

更新 engram/service.py、engram/server/app.py、engram/consolidate/outcomes.py 及完整 frontend/dist。保留远端检索核心、配置、环境变量、数据和独立私人实例。部署源基于当前本地工作树，保留未提交状态，不包含 eval 或其它正在进行的工作。

线上核心旧于本地；部署包通过 legacy-defaults.patch 保留旧版默认导入提炼语义及原请求默认值，避免 None 传入旧核心后静默不提炼。补丁针对 manifest.source_head 上当前已验证的本地工作树，不代表完整仓库已升级。

## 验证

- 远端核心快照 + 本次三个后端文件：38 passed / 1 skipped（新增10项接口测试 + 线上原有服务测试）。
- 公网 health/ready 正常；index、JS、CSS 与发布 manifest 的 SHA-256 一致。
- 新会话筛选/统计、事实筛选/facets、来源默认隐藏和非法筛选422均实测通过。
- 原演示命名空间计数保持：77 episodes、117 live facts、35 superseded、77 summaries。
- 公网中文控制台浏览器实测：事实来源筛选、会话状态筛选均收到真实200响应；390px无横向溢出，运行时错误0。首次使用 networkidle 等待触发超时；改用具体控件及接口响应验收后通过，未以网络空闲作为页面可用标准。
- 服务无自动重启；私人服务原 PID 和健康状态保持。

## 备份与回滚

发布目录：/home/ubuntu/engram-releases/console-20261029-684b334d59c7。

备份目录：/home/ubuntu/engram-backups/console-20261029-684b334d59c7，权限0700。服务停止时完整备份2675个数据文件，源目录与备份的逐文件哈希聚合一致；更新代码期间数据未变化，环境文件哈希保持。

普通代码回滚：停止 engram.service；从备份恢复三个后端文件和 full-dist；重新启动并验证 ready。保留现有数据，不用旧数据备份覆盖可能已有的新写入。仅在明确数据恢复事项下使用 data 备份。不要操作独立的 engram-personal 服务。

生产更新没有修改检索算法或进行新的基准测量，不能据此宣称准确率/性能提升。
