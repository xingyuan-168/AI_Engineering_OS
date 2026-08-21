# Agent 实现规格

版本：V2.0-derived-agent
状态：可执行实现规格基线

## 1. Agent 定义

Agent 是带角色、权限和产物责任的任务执行单元。Agent 不拥有项目全局权限；所有任务必须由 Workflow Engine 创建，并在独立 Branch/Worktree 中执行。

## 2. 标准配置

```yaml
schema_version: "1.0"
name: architect
version: "1.0.0"
role: Architect
purpose: 负责系统架构、API、数据库和重大技术决策
skills: [architecture-design, api-design, database-design]
read_paths: [docs/, .codex-os/]
write_paths: [docs/ARCHITECTURE.md, docs/API_SPEC.md, docs/DATABASE.md, docs/ADR/]
command_profile: documentation
branch_prefix: agent/architect/
requires_review: true
max_concurrency: 1
```

## 3. 角色边界

| Agent | 主要产物 | 允许修改 | 必须 Review |
| --- | --- | --- | --- |
| Product Manager | 需求、用户故事、范围 | 产品和业务文档 | 是 |
| Architect | 架构、API、数据库、ADR | 设计文档 | 是 |
| Frontend Engineer | UI 实现和组件 | 前端授权目录 | 是 |
| Backend Engineer | 服务和接口实现 | 后端授权目录 | 是 |
| Database Engineer | Schema、迁移、索引 | 数据库授权目录 | 是 |
| QA Engineer | 测试和质量报告 | 测试目录、报告目录 | 是 |
| Security Engineer | 安全报告、策略建议 | 安全文档、报告目录 | 是 |
| Reviewer | Review 结论 | Review 记录 | 否，负责最终审阅 |

## 4. 任务契约

```yaml
task_id: TASK-001
workflow_id: WF-001
agent: architect
input_artifacts: [docs/SCOPE.md]
expected_outputs: [docs/ARCHITECTURE.md]
allowed_paths: [docs/ARCHITECTURE.md, docs/ADR/]
worktree: .worktrees/WF-001/architect/TASK-001
branch: agent/architect/TASK-001
review_required: true
deadline_seconds: 1800
```

任务完成必须提交输出产物、变更文件、测试结果、命令日志和假设列表，并按 [AGENT_HANDOFF.md](AGENT_HANDOFF.md) 生成结构化交接包。交接包必须包含生产者、消费者、Workflow/Task、产物路径和 hash、Commit、测试、风险及开放问题；聊天文本不能替代交接产物。

## 5. 消息事件

Agent 之间只传递结构化事件：

```yaml
event_id: EVT-001
task_id: TASK-001
workflow_id: WF-001
from: project-manager
to: architect
type: task.created | task.completed | task.failed | review.requested
payload: {}
artifacts: []
created_at: RFC3339
```

不使用隐式对话内容作为唯一事实来源；关键结论必须写入文档、ADR 或事件日志。

## 6. Worktree 规则

- 每个任务创建独立 Branch 和 `.worktrees/<workflow-id>/<agent-name>/<task-id>`。
- Agent 只能写入 `write_paths` 与当前 Worktree 的允许目录。
- 任务完成后由 Reviewer 审阅，合并前运行测试和安全检查。
- 合并冲突、脏工作树、路径越界和未提交变更都会阻塞任务。
- 任务取消时保留分支、日志和产物索引，按清理策略人工回收。

## 7. 交接验收

消费者必须在接受任务前校验交接包中的必需产物、来源 Commit、hash、测试结果、允许路径和开放问题。缺少产物、hash 不匹配、测试未执行、路径越界或开放问题未处理时，必须返回 `blocked`，不能自行补猜或直接推进 Workflow。交接状态与 `handoff_id` 必须写入事件和 SQLite。

## 8. 失败与回收

- 超时：停止容器，保留日志，标记 `failed`。
- 权限拒绝：不重试，标记 `blocked` 并提示策略原因。
- 输出缺失：允许一次修复请求，仍缺失则退回任务。
- Review 退回：原 Agent 在原 Worktree 修复，不创建无记录副本。
- Agent 不可用：由 Agent Manager 重新调度同角色，不改变任务 ID。

## 9. 完成定义

Agent 只有在角色、权限、输入输出、Worktree、事件、Review、超时和回收规则均明确，并有越权、失败和合并冲突测试时才算完成。
