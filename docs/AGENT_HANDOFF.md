# Agent 交接规范

版本：V2.0-derived-handoff
状态：可执行实现规格基线

## 1. 交接原则

Agent 之间通过结构化产物和事件交接，不以聊天文本作为唯一事实来源。交接必须可追踪到 Workflow、Task、Branch、Commit、文档 hash 和测试结果。

## 2. 统一交接包

```yaml
handoff_id: HANDOFF-20260831090003000000-D4E5F607
workflow_id: RUN-20260831090000000000-A1B2C3D4
task_id: TASK-20260831090001000000-B2C3D4E5
producer: architect
consumer: backend-engineer
status: ready | accepted | rejected | blocked
input_artifacts: []
output_artifacts: []
changed_files: []
commit_refs: []
tests:
  commands: []
  result: passed | failed | not_run
assumptions: []
open_questions: []
risks: []
review_required: true
created_at: RFC3339
```

每个 artifact 必须包含路径、类型、hash、来源 Commit 和状态。交接包本身写入事件和产物索引。

## 3. 角色交付

| 生产者 -> 消费者 | 最小交付 |
| --- | --- |
| Product Manager -> Architect | `PRODUCT_REQUIREMENTS.md`、`USER_STORY.md`、`BUSINESS_RULES.md`、`SCOPE.md` |
| Product Manager -> Frontend Engineer | `PRODUCT_DESIGN.md`、已批准需求、用户与验收状态 |
| Frontend Engineer（设计） -> Frontend Engineer（原型） | `INTERACTION_DESIGN.md`、`UI_DESIGN.md`、状态/可访问性要求 |
| Frontend Engineer（原型） -> 用户/Reviewer | 离线 HTML 原型、入口文件、Commit/hash、`html-prototype-validator` 证据 |
| 用户/Reviewer -> Frontend Engineer（实现） | accepted `ux-prototype` Review、绑定 Commit/hash、允许实现路径 |
| Architect -> Backend/Frontend/Database | `ARCHITECTURE.md`、`API_SPEC.md`、`DATABASE.md`、相关 ADR |
| Backend/Frontend/Database -> QA | 修改文件、Commit、测试命令/结果、已知风险、迁移说明 |
| QA/Security -> Reviewer | 测试报告、安全报告、未解决问题、阻塞建议 |
| Reviewer -> Release Manager | Review 结论、合并 Commit、发布建议、回滚风险 |

## 4. 接收校验

消费者必须检查：

- 必需产物存在且 hash 匹配。
- 产物来源 Commit 与交接任务一致。
- 测试已执行且结果符合门禁。
- 开放问题、假设和风险有明确处理方式。
- 允许路径和目标分支正确。

缺少产物、hash 不匹配、测试未执行、开放问题未回答或路径不合法时，消费者必须拒绝接收并返回 `blocked`，不得自行补猜。

## 5. 交接状态

`ready -> accepted` 表示接收；`ready -> rejected` 表示格式或证据不合格；`ready -> blocked` 表示依赖、审批或环境不足。每次状态变化写入 `handoff_id` 关联事件。

## 6. 完成定义

交接规范只有在 Schema、角色矩阵、hash/Commit 校验、拒绝条件、阻塞恢复和审计关联均有测试时才算完成。
