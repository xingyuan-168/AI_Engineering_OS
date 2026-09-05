# Codex Plugin 规范

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"architect","requirement_refs":["REQ-1.6.2","VERSION-001"]} -->

版本：V2.0-derived-plugin
状态：可执行实现规格基线
当前宿主：Codex Host；DeepSeek Harness 仅作为未来适配器。

## 1. 定位

Plugin 是 Codex Host 与 AI Engineering OS 本地 CLI Runtime 之间的适配层。Plugin 提供 Skill 发现、命令入口和 Hook 转发，但不拥有项目事实、Workflow 状态或执行安全策略。

## 2. 宿主与插件职责

### Codex Host 负责

- 模型调用和 Session 管理。
- Tool Runtime、Agent 对话上下文和宿主交互。
- 宿主级权限和用户确认界面。

### AI Engineering OS 负责

- 项目管理、Workflow、Skill 和 Agent 配置。
- 文档治理、Memory、审批、审计和发布门禁。
- SQLite 状态、事件、产物索引和 Docker/Podman 执行策略。

Plugin 不得直接绕过 CLI Runtime 修改项目文件、SQLite 状态或执行高风险命令。

## 3. 生命周期

`discovered -> installed -> enabled -> running -> disabled -> upgraded/uninstalled`。

- 安装前校验 manifest、版本、权限和兼容矩阵。
- 启用时注册 Skill 和 Hook，失败则保持 disabled。
- 停用不得删除项目文档、状态库或审计数据。
- 升级前保存 Plugin 配置和版本，失败可恢复上一版本。
- 卸载只删除 Plugin 自身文件，不删除用户项目数据。

## 4. Host Hook 与内部事件

Codex Host Hook 使用宿主支持的生命周期事件，包括 `SessionStart`、`SessionEnd`、`PreToolUse`、`PermissionRequest`、`PostToolUse`、`PreCompact`、`PostCompact`、`UserPromptSubmit` 和 `Stop`。V1 插件只注册 `SessionStart` 和 `PreToolUse`：加载项目上下文，并在工具调用前执行只读、尽力而为的策略提示和明显破坏命令过滤。Runtime 审计来自结构化应用服务和 Host Operation，不依赖未注册的 `PostToolUse` Hook。

`workflow.started`、`workflow.paused`、`task.completed`、`approval.requested` 和 `release.candidate` 等名称属于 OS 内部事件总线，追加写入 SQLite 后可由 MCP 查询；不得写入 Codex Hook 配置并冒充 Host 事件。

Host Hook 必须幂等、可超时、可失败隔离。Plugin Hook 未经用户信任时视为未启用；Hook 失败不得回滚已提交的核心状态。Execution Manager 始终执行最终授权，不能把 Hook 当作唯一安全边界。

## 5. 调用接口

Plugin 向 CLI Runtime 提交结构化请求：

```yaml
request_id: REQ-20260831090004000000-E5F60718
api_version: "1.2"
operation: project.init | workflow.start | workflow.status | workflow.step
           | workflow.resume | approval.submit | task.complete
           | docs.check | verification.prepare | verification.run
           | host_operation.execute | host_operation.reconcile
           | database.migrate | release.candidate.create
           | memory.submit | memory.review | memory.search
project_id: PROJECT-001
workflow_id: RUN-20260831090000000000-A1B2C3D4
payload: {}
idempotency_key: string
```

Runtime 返回 `api_version=1.2`、request/correlation ID、双轴状态、`state_version`、`next_actions`、兼容 `next_action`、warnings、data 和结构化 error。Plugin 只展示或转发结果，不自行创建状态。

## 6. 权限边界

Plugin 默认只能读取 Skill/Agent 元数据和显示 Runtime 返回值。写项目文件、写 SQLite、执行命令、访问网络、读取凭据必须通过 Runtime 授权；项目 `.codex-os/` 只能收紧权限，不能放宽全局安全策略。项目 `.codex/` 只保存 Host 配置和 Hook。

## 7. 版本兼容

| Plugin API | CLI Runtime | 结果 |
| --- | --- | --- |
| 同主版本、支持的次版本 | 支持 | 正常启用 |
| 主版本不同 | 任意 | 禁止启用 |
| Plugin 次版本更高 | Runtime 较旧 | 禁止启用并提示升级 |
| Plugin 次版本较低 | Runtime 较新 | 仅在兼容窗口内启用 |

兼容矩阵写入 manifest 和 `CHANGELOG.md`；Hook Schema 变更必须增加版本字段。

0.2.0 的 Plugin API 为 1.2。1.0/1.1 入口只在 0.2.x 兼容窗口内保留并返回弃用 warning；最早在 0.3.0 经 ADR 移除。

## 8. DeepSeek Harness 适配边界

V1 不验收 DeepSeek Harness。未来适配通过 `HarnessAdapter` 抽象接入，只映射模型调用、Session、Tool Runtime 和宿主事件，不改变 Workflow、Skill、Agent、Memory、审批或执行策略。

## 9. 完成定义

Plugin 规范只有在生命周期、Hook、调用协议、权限、版本兼容、失败隔离、卸载保留和未来适配边界均有测试场景时才算完成。
