# 模块边界与权限规范

版本：V2.0-derived-boundary
状态：可执行实现规格基线

## 1. 职责矩阵

| 模块 | 可以做 | 禁止做 |
| --- | --- | --- |
| Project Manager | 分析目标、判断复杂度、选择 Workflow | 修改业务代码、绕过门禁 |
| Workflow Engine | 编排状态、创建任务、管理恢复 | 决定领域方案、直接改文件 |
| Skill | 生成专业产物、运行声明的检查 | 修改全局状态、扩大自身权限 |
| Agent | 在授权 Worktree 执行任务 | 访问其他 Worktree、直接发布 |
| Execution Manager | 受控容器执行、收集日志和产物 | 宿主机任意命令、绕过审批 |
| Document Manager | 生成、检查和索引文档 | 静默覆盖决策、删除历史 |
| Memory Manager | 写入、索引和检索记忆 | 保存 Secret、伪造来源 |
| Approval Manager | 请求、记录和验证审批 | 代替用户作高风险决定 |
| Release Manager | 汇总证据、生成候选物 | 无 G4 执行生产发布 |
| Codex Host | 模型推理、Session 和 Tool Runtime | 直接绕过本地策略写项目 |

## 2. 权限归属

- 控制流归 Workflow Engine。
- 领域能力归 Skill。
- 角色执行归 Agent。
- 文件和命令权限归 Execution Manager。
- 事实归 Markdown/Git。
- 状态归 SQLite。
- 审批归人工和 Approval Manager。
- 路由建议归 Project Manager，最终状态转换归 Workflow Engine。
- 记忆来源和生命周期归 Memory Manager，不能反向修改事实文档。

## 3. 交叉边界

- Project Manager 可以提交路由建议，但不能直接推进 G1-G4。
- Skill 可以生成产物，但必须通过 Document Manager 写入并关联任务。
- Agent 可以请求执行，但只能由 Execution Manager 决定命令和路径是否允许。
- Plugin 可以转发 Hook，但不能持有 Workflow 真相或放宽安全字段。
- Memory 可以检索辅助信息，但低置信度记录不能直接变成需求、架构或安全事实。

## 4. 冲突处理

权限冲突、安全等级冲突、路径冲突和事实冲突优先进入 `blocked`。任何模块不得静默覆盖其他模块的状态、文档、权限或审计记录；必须通过审批、ADR 或变更影响检查解决。

## 5. 完成定义

边界规范只有在职责矩阵、权限归属、交叉调用、冲突处理和越权拒绝场景均有测试时才算完成。
