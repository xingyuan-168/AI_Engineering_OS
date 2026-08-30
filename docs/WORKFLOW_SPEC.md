# Workflow 实现规格

版本：V2.0-derived-workflow
状态：可执行实现规格基线

## 1. 工作流阶段与运行状态

`workflow_phase` 表示业务进度：

| 阶段 | 进入条件 | 必须产物 | 失败处理 |
| --- | --- | --- | --- |
| `intake` | 已有业务目标 | 项目清单、问题清单、Routing Decision | `blocked` |
| `requirements` | G0 通过 | 产品需求、用户故事、业务规则 | `blocked` |
| `research` | G1 通过 | 开源研究、License 记录 | `blocked` |
| `design` | G1 通过且研究输入齐全 | 架构、技术栈、API、数据库、安全、UI（如适用） | `blocked` |
| `prototype` | 含 `frontend-project` 且设计任务完成 | 离线 HTML 交互原型、validator 证据、独立 UX 确认 | `blocked` |
| `implementation` | G2 通过且 Agent 交接依赖满足 | 代码提交、任务产物、Handoff | `failed` |
| `verify` | 实现任务完成 | 测试、Review、安全扫描 | `failed` |
| `release` | G3 通过 | 发布候选物、变更记录、回滚包 | `blocked` |
| `memory` | 发布候选物完成 | ADR、失败记录、索引 | `failed` |
| `completed` | G4 通过且 Memory 完成 | 完整证据包 | 终态 |

`run_status` 表示执行生命周期：

| 状态 | 含义 |
| --- | --- |
| `created` | 已创建但尚未开始 |
| `running` | 当前阶段正在执行 |
| `needs_approval` | 等待指定审批，不允许自动超时通过 |
| `paused` | 用户或策略暂停，检查点已保存 |
| `blocked` | 信息、依赖、安全条件或合并冲突未满足 |
| `failed` | 执行失败且自动重试已耗尽或不适用 |
| `cancelled` | 用户明确取消 |
| `completed` | `workflow_phase=completed` 且全部证据齐全 |

## 2. 合法转换

```text
backend: intake -> requirements -> research -> design -> implementation
frontend/fullstack: intake -> requirements -> research -> design -> prototype -> implementation
implementation -> verify -> release -> memory -> completed

run_status: created -> running
running -> needs_approval | paused | blocked | failed | completed
needs_approval -> running | blocked | cancelled
paused -> running | blocked | cancelled
blocked -> running | cancelled
failed -> running | cancelled
```

阶段与运行状态必须在同一 `state_version` 事务中更新。禁止跳过顺序进入 `implementation`、`release` 或 `completed`。`bug-fix` 可从 `intake` 进入 `requirements` 的精简分支，但仍必须完成影响检查和 G3 质量验证。

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

基础 Workflow 与 Profile 独立组合：`large-project` 提供多 Agent、Worktree 和额外 Review 门禁；`frontend-project` 启用 UX Research、UI Spec、离线 HTML 交互原型、validator 和独立用户确认；`backend-project` 启用 API、数据库、迁移和服务测试链路。Profile 只能增加步骤和证据，不能跳过 G0-G4 或降低执行安全等级。项目类型或 impact paths 表明存在前端页面时，Routing 必须保留 `frontend-project`，人工 override 不能移除原型门禁。

前端原型由 `frontend-engineer` 使用 `html-prototype` Skill 生成在 `docs/prototypes/<prototype-id>/index.html`。原型必须 UTF-8、自包含、断网可运行，并覆盖成功、空态、加载、验证、权限、失败、重试、取消和恢复状态。`prototype_review_submit` 只从任务绑定 Commit 读取原型并复算 hash；生产者不得自审。只有 `html-prototype-validator` 通过且独立 `ux-prototype` Review accepted 后，G2 才可能通过。原型 Commit、文件或 hash 变化会使旧 Review stale。

Profile Schema 1.2 声明任务模板、影响路径模式、增量 Gate 证据和 Reviewer。Runtime 将核心 Gate 与所选 Profile 编译为不可放宽的 `EffectiveGovernancePolicy` 并保存 policy hash；任务允许路径从已批准 impact paths 匹配产生，重叠路径按稳定顺序建立依赖，未映射路径阻塞。G0 bundle 必须绑定 Routing Decision，G2 bundle 必须包含 Migration Spec 与适用 ADR 索引。

G2 批准事务只写入审批、绑定的 evidence/policy/routing hash 与 `integration_prepare` Host Operation；创建集成 Worktree、任务组和 Agent Worktree 由后续 executor 完成。executor 重试会复用登记 Worktree/任务组，未登记但与 base Commit/分支完全一致的受管 Worktree 可重新登记；Git 结果不确定时进入 `reconcile_required`。

G3 批准同样只在事务中写入审批、验证缓存绑定和 `release_prepare` Host Operation。executor 幂等建立 Release task/Worktree，在断网 OCI 中挂载已批准 wheelhouse，使用 integration Commit 时间作为 `SOURCE_DATE_EPOCH` 构建 wheel、sdist 和 Plugin 包；制品先进入 operation 专属 staging，完整复算 hash 后原子提升为 candidate。staging 残留必须人工保留并对账；已提升但未入库的 candidate 仅在重试且 manifest/source/lock/全部文件 hash 一致时恢复索引。

G4 审批调用只执行只读 PR、merge Commit、target ancestry、候选 manifest 与发布 authority 预检，并在审批事务中写入 `release_publish` Host Operation；不得创建 tag、上传资产或完成 Workflow。executor 重新校验相同意图并完成远端对账，先将 operation 标记 succeeded，再在受约束事务中把 Workflow 置为 `completed`；任一步结果未知都保持 `reconcile_required`。

## 5. 四条 V1 Workflow

### `new-project`

后端为 `intake -> requirements -> research -> design -> implementation -> verify -> release -> memory`；含前端页面时强制为 `intake -> requirements -> research -> design -> prototype -> implementation -> verify -> release -> memory`。

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
