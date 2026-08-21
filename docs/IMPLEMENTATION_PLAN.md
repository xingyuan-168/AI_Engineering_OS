# Codex AI Engineering OS 实施计划

版本：V2.0-derived
状态：实施中
实施方式：个人本地 MVP，纵向闭环优先，关键门禁人工确认，每个逻辑变更提交并立即推送。

## 1. 实施结果

交付一个可在本地 Git 仓库运行的最小系统：用户输入业务目标后，系统能初始化文档、选择 Workflow、执行开源调研、拆分任务、调度 Agent、在受控 Worktree 中完成开发，并输出测试、Review、安全和发布记录。

## 2. Git 交付协议

- OS 主仓库远端固定为 `git@github.com:xingyuan-168/AI_Engineering_OS.git`。
- 每个完整逻辑变更一个 Conventional Commit，验证后立即推送当前里程碑分支。
- 完成事件必须关联 Branch、Commit SHA、远端推送状态、产物 hash 和验证结果。
- 禁止 force push 或改写已推送历史；错误通过新提交或 `git revert` 修正。
- SQLite 运行状态、日志、缓存和临时 Worktree 不进入版本库。

## 3. 里程碑计划

### M0：仓库与规格基线

任务：

- 绑定并审计 GitHub 远端，建立 `main` 文档基线和里程碑分支。
- 明确 `workflow_phase`/`run_status`、G0-G4、Host Hook/内部事件、Skill 路径和迁移语义。
- 固定 Python 3.12、uv 和 Docker Desktop 前置条件；环境缺失必须由 `doctor` 明确阻塞。
- 新增 `AGENTS.md` 与 ADR-0002，固化事实源和 Git 交付规则。

产物：可推送仓库基线、收敛后的规格、仓库指令、环境预检和 ADR。

完成条件：没有未处理的范围冲突或关键术语冲突；远端可推送；Docker 未就绪时明确记录环境阻塞而不降低安全策略。

### M1：确定性内核与文档治理

任务：

- 创建 Python 包、严格 Pydantic 配置、ID、SQLite 迁移和追加式事件存储。
- 实现原子文件写入、内容 hash、文档完整性、链接和影响检查。
- 实现 `doctor`、`init`、`status` 和 `check-docs`。
- 配置按内置→Plugin→用户级→项目级→Workflow→CLI 合并；下层只能收紧安全策略。

产物：可安装 CLI、Schema 迁移、运行存储、文档模板和治理报告。

完成条件：空仓库初始化幂等；未知配置失败；数据库可恢复；文档错误可定位。

### M2：`new-project` 工作流

任务：

- 实现双轴状态机、G0-G4、检查点、暂停、恢复、有限重试和乐观并发。
- 实现任务、制品、审批、Handoff、`next_action` 和 `task_complete` Git 证据校验。
- 先以 Fake Host 适配器覆盖全流程，不依赖模型调用。

产物：`new-project` Workflow、事件、审批和恢复测试。

完成条件：任意阶段中断可恢复，审批不可绕过，重复输入不产生重复任务或制品。

### M3：Codex Plugin 与混合编排

任务：

- 创建 `.codex-plugin/plugin.json`、repo marketplace、Plugin Skills、stdio MCP server 和可信 Hooks。
- 实现 Host `next_action/task_complete` 握手和 MCP 工具 Schema。
- 可选 `CodexExecAdapter` 仅在 `doctor` 验证 CLI 与认证后启用。

产物：可验证、可私有安装的 Codex Plugin 和 MCP 集成测试。

完成条件：可从 Codex 启动、审批、恢复并完成 fixture 工作流；Host Hook 不取代 Runtime 授权。

### M4：Agent、Worktree 与执行沙箱

任务：

- 实现 Product、Architect、Backend、Database、QA、Security 和 Reviewer Agent 配置。
- 实现任务 Branch/Worktree、路径与 junction/symlink 边界、dirty/conflict 阻塞和 Git 证据。
- 实现 Docker 固定镜像、非 root、默认断网、只读根文件系统、显式挂载和资源限制。
- 实现 L0-L4 风险策略；无沙箱时拒绝写入和高风险执行。

产物：Skill/Agent 契约、Worktree 管理器、Execution Manager、安全策略。

完成条件：Agent 不能修改未授权路径；所有任务都能关联输入、输出、分支、提交和 Review。

### M5：ERP 采购模块试点与验收

任务：

- 从业务目标启动 `new-project`。
- 完成需求、范围、开源调研、架构、技术栈、API、数据库和安全文档。
- 通过 Worktree 执行一个完整功能切片。
- 运行测试、Review、安全扫描和发布候选物流程。
- 将重大决策、失败方案和发布结果写入 Memory。
- 验证 Decision、Project、Bug、Experience 四类 Memory 的来源 hash、脱敏、检索、失效和跨项目隔离。

产物：试点项目、端到端日志、验收报告、问题清单和改进 ADR。

完成条件：从目标输入到发布候选物的全过程可重放、可审计、可恢复。

### M6：完整 V1 扩展

任务：在纵向试点稳定后增加 `feature-development`、`bug-fix`、`release` Workflow，补齐 frontend/backend/large Profile、剩余 Skill、Memory 生命周期与 Podman 适配器。

产物：完整 V1 Workflow/Skill/Profile 集、兼容测试和发布包。

完成条件：扩展能力不降低 G0-G4、Git 证据、Worktree 隔离或默认沙箱规则。

## 4. 推荐命令面

```text
codex-os init
codex-os run new-project --goal "开发一个 ERP 采购模块"
codex-os status
codex-os resume <workflow-id>
codex-os check-docs
codex-os research <capability>
codex-os verify
codex-os release --candidate
```

## 5. 测试计划

### 单元测试

- 项目、Workflow、Skill、Agent 配置校验。
- 状态转换、前置条件、重试和恢复。
- 文档影响判定和必需产物检查。
- 命令风险、路径权限和人工审批判定。

### 集成测试

- 一句业务目标生成完整项目骨架。
- 功能修改正确触发 API/数据库/架构/ADR/CHANGELOG 检查。
- Agent 在独立 Worktree 中执行并通过 Review 合并。
- 外部 License 未确认时阻止依赖进入技术栈。
- 失败任务可以恢复，重复执行不会产生复制目录。

### 验收指标

- 文档完整率：100%。
- 关键门禁绕过次数：0。
- 发布候选物具备完整测试、Review 和安全证据。
- 所有开源采用结论均有版本和 License 记录。
- 试点 Workflow 能够中断并恢复。

## 6. 主要风险与应对

| 风险 | 应对 |
| --- | --- |
| 自动化范围过大 | 先做文件化状态机和关键门禁，逐步增加自动化 |
| 多 Agent 冲突 | 强制 Branch/Worktree 和路径边界，禁止共享写入 |
| 外部项目许可证或升级风险 | 上游核验、版本固定、许可证清单和 ADR |
| 记忆污染或失去来源 | 事实写入 Markdown/Git，检索结果保留来源和时间 |
| 命令执行造成破坏 | allowlist、dry-run、人工确认和完整日志 |
| 文档与实现漂移 | CI 文档检查、变更影响检查和发布门禁 |

## 7. Definition of Done

一个阶段只有在代码、文档、测试、Review、风险记录、提交 SHA 和远端推送证据均完成后才算结束。任何“代码已完成但文档未更新”或“变更未提交推送”的状态都视为未完成。

## 8. 责任与证据

| 阶段 | 责任角色 | 必须保留的证据 |
| --- | --- | --- |
| 规格冻结 | Product Manager + 用户 | 评审结论、范围变更记录、基线提交 |
| 文档治理 | Project Manager | 文档检查报告、影响检查报告 |
| 开源研究 | Architect + Security | 官方来源、版本、License、风险记录、决策 ADR |
| Workflow MVP | Architect + Execution Manager | 状态转换测试、检查点、事件日志、恢复记录 |
| Agent/Skill | Agent Manager + Reviewer | 配置、任务输入输出、Worktree、Review 结果 |
| 试点验收 | QA + Security + 用户 | 端到端日志、测试报告、安全报告、发布候选物 |

任何阶段缺少责任角色或证据，状态必须保持为 `blocked`，不得标记完成。
