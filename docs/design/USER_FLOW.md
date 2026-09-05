# 用户流程

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"frontend-engineer","requirement_refs":["REQ-1.6.2","FRONTEND-001"]} -->

状态：可执行设计基线

## 角色

| 角色 | 目标 | 主要权限 |
| --- | --- | --- |
| 项目发起人 | 提出目标、确认范围和关键门禁 | 创建项目、审批 G0/G1/G2/G4 |
| Product Manager | 澄清需求、选择流程和验收标准 | 编辑产品文档、拆分需求 |
| Architect | 设计模块、数据流、技术栈和 ADR | 编辑架构文档、提交技术决策 |
| Engineer Agent | 在授权 Worktree 中实现任务 | 修改授权路径、运行低风险命令 |
| QA/Security | 验证质量、安全、依赖和发布证据 | 执行检查、阻止不合规发布 |
| Reviewer | 审阅产物并决定合并 | Review、批准或退回任务 |

## 主流程

```text
目标输入
  -> 澄清问题
  -> G0 范围确认
  -> 需求与开源调研
  -> G1 需求确认
  -> 架构、技术栈、API/数据库、UI 设计
  -> G2 设计确认
  -> 任务拆分与 Worktree
  -> 实现与测试
  -> Review / Security
  -> G3 质量通过
  -> G4 发布确认
  -> 发布候选物与经验沉淀
```

## 关键分支

- 信息不足：进入 `blocked`，显示问题、所需回答和影响范围。
- 文档冲突：停止编码，生成文档影响报告。
- License 或安全风险：禁止进入实现，转人工决策。
- 测试失败：保留日志，允许重试；超过上限进入人工诊断。
- Review 退回：任务回到对应 Agent，不得直接跳到发布。
- 发布拒绝：保留候选物和理由，Workflow 可从 G4 恢复。

## 完成定义

每条用户流程都必须有成功、空态、错误、阻塞、审批、恢复和完成状态；状态变化必须能关联 Workflow ID、任务 ID 和文档/日志证据。
