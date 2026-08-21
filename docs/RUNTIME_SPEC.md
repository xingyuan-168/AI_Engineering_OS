# 运行时实现规格

版本：V2.0-derived-runtime
状态：可执行实现规格基线
范围：Windows 本地 CLI、Codex 宿主适配、Workflow、Skill、Agent、执行沙箱和本地状态。

## 1. 目标与边界

运行时负责项目状态、文档治理、Workflow 编排、任务调度、审批、沙箱执行、审计和恢复。AI 推理由 Codex 宿主负责；运行时不在 V1 自行实现模型客户端、模型选择器或远程多租户服务。

运行时不得绕过 `DocumentManager`、`ApprovalManager` 和 `ExecutionManager` 直接修改项目文件或运行命令。

## 2. 组件职责

| 组件 | 输入 | 输出 | 不负责 |
| --- | --- | --- | --- |
| CLI Adapter | 命令行参数、当前目录 | 命令结果、退出码 | 直接执行业务命令 |
| Codex Adapter | Skill/Agent 请求、宿主结果 | 标准化任务结果 | 管理项目状态 |
| Project Manager | 业务目标、项目配置 | 项目类型、风险、Workflow 建议 | 直接改代码 |
| Workflow Engine | Workflow 定义、事件、审批 | 状态转换、任务、检查点 | 领域内容决策 |
| Document Manager | 文档模板、变更 diff | 文档、完整性和影响报告 | 代替人工批准 |
| Agent Manager | Agent/Task 配置 | 任务分发、Worktree、合并请求 | 绕过权限策略 |
| Execution Manager | 执行请求、Policy | 容器执行结果、日志、产物 | 宿主机任意执行 |
| Memory Manager | ADR、事件、失败记录 | 可检索记录和来源 | 修改事实来源 |
| Release Manager | 验证结果、产物 | 发布候选物、校验和、回滚包 | 无审批自动生产发布 |

## 3. 生命周期

```text
created -> running -> waiting_approval -> paused
                      |                       |
                      v                       v
                    failed <-------------- resumed
                      |
                      v
                  completed
```

### 生命周期规则

- `created`：已创建 Workflow，但未开始执行。
- `running`：当前步骤正在执行，只允许一个活动状态转换。
- `waiting_approval`：等待指定门禁的人工决定，不能自动超时通过。
- `paused`：用户或策略主动暂停，保留检查点和未完成任务。
- `failed`：步骤失败或超过重试上限，必须保留失败事件和恢复建议。
- `completed`：所有产物、验证、审批和记忆记录齐全。
- `cancelled`：用户明确取消；与失败不同，不自动重试。

## 4. 运行上下文

每次运行必须携带：

```yaml
run_id: RUN-20260820-0001
project_id: PROJECT-001
workflow_id: WF-001
task_id: TASK-001
parent_run_id: null
config_revision: git:abc123
actor: user | codex-host | agent
```

`run_id`、`workflow_id`、`task_id` 和 `config_revision` 写入所有日志、事件、审批和产物索引。

## 5. 事件与 Hook 生命周期

运行时必须为以下事件生成追加式事件记录，并按 [PLUGIN_SPEC.md](PLUGIN_SPEC.md) 转发可用 Hook：

| 事件 | 生成时机 | 必须关联 |
| --- | --- | --- |
| `routing.decided` | intake 完成评分、Profile 和审批判断 | 输入 hash、评分、理由、覆盖信息 |
| `worktree.created` / `worktree.cleaned` | Worktree 创建或人工确认回收 | workflow、agent、task、branch、path |
| `handoff.created` / `handoff.accepted` / `handoff.blocked` | 交接包创建和消费者验收 | handoff、产物 hash、Commit、测试结果 |
| `memory.candidate` / `memory.activated` / `memory.invalidated` | Memory 候选、激活或来源失效 | memory_id、source_hash、状态原因 |
| `plugin.hook.failed` | Plugin Hook 超时、拒绝或异常 | hook、request_id、重试次数、错误码 |

事件 payload 必须可脱敏、可重放且包含 `project_id`、`workflow_id`、`task_id`。核心状态先提交到 SQLite，再异步转发 Plugin Hook；Hook 失败只影响通知，不回滚核心状态。

## 6. 幂等与恢复

- 状态转换键为 `workflow_id + state_version + transition_name`，相同键重复提交不得创建重复任务。
- 产物写入先写临时文件，再原子替换；已有相同内容 hash 的产物复用索引。
- 每个可恢复状态完成后写检查点，检查点包含状态、配置版本、任务快照和产物引用。
- 恢复时重新校验工作树、配置 hash、数据库迁移版本和容器可用性。
- 不允许从未知配置版本恢复；必须先执行迁移或进入 `blocked`。

## 7. 重试与失败

- 默认最多重试 2 次，间隔 5 秒和 30 秒；人工审批、License 风险和权限拒绝不得自动重试。
- 失败事件必须包含错误码、退出码、命令摘要、日志引用、是否可重试和建议动作。
- 同一任务连续失败超过上限进入 `failed`，Workflow 进入 `blocked`，等待人工处理。

## 8. Windows 运行约束

- 路径统一使用绝对路径校验后再转换为容器挂载路径。
- 命令通过受控 subprocess 执行，不使用未经解析的 shell 拼接。
- 所有外部进程设置超时、工作目录、环境变量白名单和标准输出/错误流采集。
- 处理 UTF-8 输入输出；无法解码的外部输出保存原始字节并标记编码。
- Docker Desktop 或 Podman 不可用时，高风险执行必须阻塞，不得静默降级到宿主机。
- Codex Host 或 Plugin 不可用时，已提交的 Workflow 状态、检查点和事件必须保留；Runtime 进入 `paused` 或 `blocked`，恢复后从最后检查点继续，不重复执行已确认的幂等步骤。
- Worktree、Handoff、Routing 和 Memory 的失败均保留现场、错误码和人工恢复建议。

## 9. 退出码

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 参数或配置错误 |
| 10 | 文档不完整或影响检查未通过 |
| 20 | 需要人工审批 |
| 30 | 执行失败或测试失败 |
| 40 | 权限、路径或命令被策略拒绝 |
| 50 | 沙箱不可用 |
| 60 | 数据库迁移、备份或恢复失败 |
| 70 | 发布候选物不完整 |

## 10. 完成定义

运行时规格只有在生命周期、上下文、幂等、检查点、重试、Windows 约束、退出码和组件边界均有对应测试场景时才算完成。
