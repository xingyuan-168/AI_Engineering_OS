# 变更记录

## [Unreleased] - 2026-08-21

### Added

- 建立 Python 3.12/uv 可复现包与 Ruff、Pyright、pytest、pip-audit 质量门禁。
- 增加严格 Pydantic 配置和只能收紧的执行策略覆盖。
- 增加 SQLite WAL/外键迁移、checksum、迁移前备份和追加式幂等事件。
- 增加幂等项目初始化、原子文档写入、来源 hash 上下文和文档治理检查。
- 增加 `doctor`、`init`、`status`、`check-docs` CLI 及纯 JSON 输出契约。
- 增加 `new-project` 双轴状态机、G0-G4、事务化任务/审批/制品事件和恢复。
- 增加 `run new-project`、`step`、`approve`、`reject`、`resume` CLI。
- 增加仓库级 Codex 私有插件、marketplace 和官方 MCP Python SDK 2.x stdio 服务。
- 增加 11 个 MCP 工具及从 `project_init` 到 G4 完成的 Host 握手集成测试。
- 增加 11 个工程 Skill、7 个项目级自定义 Agent profile 和可复核的插件生命周期 Hooks。
- 增加 `task_complete` 的真实 Git/远端/制品校验、验证时间及事务化 handoff 证据。
- 增加确定性任务 Branch/Worktree 的路径、冲突、磁盘与 Git 预检；仅允许经批准清理已合并且干净的 Worktree，并保留任务 Branch。
- 将 Worktree 分配、任务 Branch/路径和 `reserved/created/blocked/cleaned` 事件事务化写入 SQLite，并用唯一索引保证每个任务只有一个分配。
- 增加 Docker 执行策略适配器：镜像 digest 锁定、非 root、断网、只读根文件系统、最小权限、显式挂载、资源/超时限制及日志脱敏。
- 增加执行审计服务与迁移，事务记录 command hash、镜像、挂载、容器、退出码、失败码、脱敏日志引用及 Worktree dirty 事件。
- 将 Workflow 与 Worktree/Git 证据闭环接通：`next_action` 返回任务 Branch/Worktree，后续任务继承前一提交，越权制品或分配失败会阻塞运行。
- 增加显式 `fixture_local_only` Git 策略，仅供无独立远端的隔离试点；普通项目继续默认并强制 `remote_required`。

### Governance

- 绑定 GitHub 远端，实施每个逻辑变更提交并立即推送的交付协议。
- 新增 ADR-0002，统一双轴状态、G0-G4、Host Hook、Skill 路径、沙箱和迁移语义。
- 新增根目录 `AGENTS.md`，区分用户请求、项目事实和原始输入文档。

### Environment

- 当前 Windows 环境已验证 Git、uv、Python 3.12 与 SQLite FTS5。
- Docker Desktop/WSL 尚未由非管理员进程安装；`doctor` 使用退出码 50 明确阻塞沙箱执行。

### Documentation baseline (2026-08-20)

- 根据 Codex AI Engineering OS V2.0 需求建立项目文档基线。
- 增加项目总文档、实施计划、技术栈、边界和开源研究文档。
- 增加产品、架构、接口、数据库、安全、测试、部署和 ADR 入口。
- 补齐 UI 设计基线、开源仓库级核验记录和文档完成定义。
- 补齐运行时、配置、Workflow、Skill、Agent、执行策略、可观测性、迁移、发布清单和 ERP 试点验收文档。
- 冻结 Windows 本地 CLI + Codex 适配层 + Docker/Podman 默认沙箱的产品边界。

## 变更记录规则

- `Unreleased` 只记录已合并或已确认的变更，不记录个人草稿。
- 每条记录说明影响范围、用户可见变化、迁移要求和关联 ADR/Issue。
- 发布时将 `Unreleased` 转为版本号和日期，并保留回滚说明。
- 文档治理、技术栈、门禁和安全策略变化必须记录，即使没有业务代码变化。

## 版本分类

- Added：新增能力或文档。
- Changed：行为、流程、接口或策略变化。
- Fixed：缺陷和错误流程修复。
- Security：安全、依赖或权限修复。
- Governance：文档、审计、门禁和流程治理变化。
