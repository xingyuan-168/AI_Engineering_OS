# Workflow 实现规格

版本：V2.0-derived-workflow
状态：可执行实现规格基线

## 1. 状态集合

| 状态 | 进入条件 | 必须产物 | 失败去向 |
| --- | --- | --- | --- |
| `intake` | 已有业务目标 | 项目清单、问题清单、Routing Decision | `blocked` |
| `requirements` | G0 通过 | 产品需求、用户故事、业务规则 | `blocked` |
| `research` | 需求和范围明确 | 开源研究、License 记录 | `blocked` |
| `design` | G1 通过 | 架构、技术栈、API、数据库、安全、UI（如适用） | `blocked` |
| `implementation` | G2 通过且 Agent 交接依赖满足 | 代码提交、任务产物、Handoff | `failed` |
| `verify` | 实现任务完成 | 测试、Review、安全扫描 | `failed` |
| `release` | G3 通过 | 发布候选物、变更记录、回滚包 | `blocked` |
| `memory` | 发布候选物完成 | ADR、失败记录、索引 | `failed` |
| `completed` | G4 通过且 Memory 完成 | 完整证据包 | 终态 |
| `blocked` | 信息、审批或依赖不足 | 阻塞报告 | 人工解除 |
| `needs_approval` | 路由边界、用户覆盖或高风险冲突 | 路由报告、影响分析、审批请求 | 人工批准、拒绝或回到 `intake` |
| `failed` | 不可自动恢复的错误 | 失败报告 | 人工重试或取消 |

## 2. 合法转换

```text
intake -> requirements -> research -> design -> implementation
implementation -> verify -> release -> memory -> completed
任一活动状态 -> blocked（缺信息/审批/依赖）
intake -> needs_approval（路由边界、冲突或用户覆盖）
needs_approval -> intake（批准后重新确认路由，拒绝则 blocked）
implementation/verify/memory -> failed（超过重试或不可恢复）
blocked -> 原状态（解除条件满足）
failed -> 原状态（人工确认重新执行）
```

禁止跳过顺序进入 `implementation`、`release` 或 `completed`。`bug-fix` 可从 `intake` 进入 `requirements` 的精简分支，但仍必须完成影响检查和 G3 质量验证。

## 3. G0-G4 门禁

| 门禁 | 必须证据 | 审批角色 | 不通过处理 |
| --- | --- | --- | --- |
| G0 | 目标、范围、成功标准、风险级别 | 用户 + Product Manager | 回到 `intake` |
| G1 | 需求、用户故事、业务规则、范围 | 用户 + Product Manager | 回到 `requirements` |
| G2 | 架构、技术栈、API、数据库、安全、开源决策 | 用户 + Architect + Security | 回到 `research/design` |
| G3 | 测试、Review、静态检查、依赖和安全扫描 | Reviewer + QA + Security | 回到 `implementation/verify` |
| G4 | 发布候选物、CHANGELOG、ADR、Memory、回滚包 | 用户 + Release Manager | 保留候选物并回到 `release` |

## 4. 路由、Profile 与 Workflow 组合

`intake` 必须先调用 [WORKFLOW_ROUTING_RULES.md](WORKFLOW_ROUTING_RULES.md)，保存评分、理由、风险级别、候选 Workflow、Profile 和人工覆盖信息。路由失败、不确定、边界分数或用户选择与风险策略冲突时进入 `needs_approval` 或 `blocked`，不得猜测。

基础 Workflow 与 Profile 独立组合：`large-project` 提供多 Agent、Worktree 和额外 Review 门禁；`frontend-project` 启用 UX Research 到 UI Spec 的设计链路；`backend-project` 启用 API、数据库、迁移和服务测试链路。Profile 只能增加步骤和证据，不能跳过 G0-G4 或降低执行安全等级。

## 5. 四条 V1 Workflow

### `new-project`

`intake -> requirements -> research -> design -> implementation -> verify -> release -> memory`。

### `feature-development`

读取现有文档和基线，执行影响检查；必要时经过 `requirements/design`，否则进入实现；必须经过 `verify` 和 `release`。

### `bug-fix`

记录复现、影响和根因；修复后必须运行回归测试。涉及 API、数据库、安全或架构时自动升级到对应设计门禁。

### `release`

读取已完成的验证证据，生成候选物、SBOM、CHANGELOG 和回滚包；未通过 G3 或缺少人工 G4 时停止。

## 6. Agent Handoff 前置条件

涉及 Agent 的状态转换必须满足：生产者已生成 [AGENT_HANDOFF.md](AGENT_HANDOFF.md) 交接包，产物路径、hash、Commit、测试结果、风险和开放问题均已填写；消费者验收为 `accepted` 后，Workflow 才能进入下一个依赖状态。交接被拒绝或阻塞时，任务和 Workflow 保持 `blocked`，禁止用聊天文本替代交接包。

## 7. 暂停、恢复与重试

- `pause` 只允许用户或安全策略触发，写入检查点后停止新任务。
- `resume` 重新校验配置、Worktree、容器、数据库版本和产物 hash。
- 自动重试最多 2 次，仅适用于临时执行失败；权限、审批、License 和配置错误不得自动重试。
- 恢复失败进入 `blocked`，输出明确的人工动作和证据位置。

## 8. 幂等与并发

- 同一 Workflow 同时只允许一个活动转换。
- 任务以 `task_id` 和输入产物 hash 幂等；相同输入不重复生成任务。
- 多 Agent 只通过独立 Worktree 交付，不共享写入。
- 合并冲突阻塞 Workflow，不自动覆盖任一分支。

## 9. 完成定义

每条 Workflow 都必须有状态图、进入/退出条件、产物、门禁、失败去向、恢复规则和测试用例；缺少任一项不得注册为可用 Workflow。
