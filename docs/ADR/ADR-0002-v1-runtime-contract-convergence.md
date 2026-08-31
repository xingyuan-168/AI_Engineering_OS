# ADR-0002：V1 运行契约收敛

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"approved","owner":"architect","requirement_refs":["REQ-1.6.2","GOV-001"]} -->

- 状态：Accepted
- 日期：2026-08-21
- 范围：Workflow、Codex Plugin、配置、执行隔离和 SQLite 迁移
- 取代：ADR-0001 第 2 条中“优先评估 LangGraph 作为实现依赖”的部分
- 后续决策：第 6 条“Docker Desktop 是默认沙箱”的单一后端表述已由 [ADR-0003](ADR-0003-governance-runtime-boundary.md) 取代；现行规则由项目策略选择 Docker 或 Podman，安全约束不变。

## 背景

实现前审计发现，文档对 Workflow 阶段与运行状态、G0-G4 位置、Codex Hook、Skill 覆盖路径、沙箱强制性和 Schema 迁移历史存在不同表述。这些差异会产生两个状态事实源，或允许宿主绕过运行时安全策略。

## 决策

1. V1 使用自有 Python 状态机；LangGraph 只作为图状态和 checkpoint 的设计参考，不加入运行依赖。
2. `workflow_phase` 与 `run_status` 是两个正交字段。阶段描述业务进度，运行状态描述执行生命周期。
3. G0 位于 intake 之后，G1 位于 requirements 之后，G2 位于 research 与 design 之后，G3 位于 verify 之后，G4 位于 release 与 memory 收口之后。
4. Codex 官方生命周期 Hook 只使用 Host 支持的事件名；`workflow.*`、`task.*` 和 `approval.*` 属于 OS 内部追加式事件，不冒充 Host Hook。
5. Plugin 自带默认 Skill；仓库新增 Skill 使用 `.agents/skills/`。`.codex/` 只保存 Codex Host 配置和 Hook，`.codex-os/` 保存 OS YAML 配置和运行数据。同名 Skill 不作为覆盖机制。
6. Docker Desktop 是 V1 写操作、代码执行、测试和高风险命令的默认沙箱。沙箱不可用时只允许低风险只读诊断。
7. Schema 迁移历史使用追加式 `schema_migrations`，当前版本由最后一条成功迁移记录推导。
8. Codex Host 通过 MCP 获取 `next_action` 并以 `task_complete` 回传结果；可选 `codex exec` 仅在 `doctor` 验证通过后执行叶子任务。

## 后果

运行时只维护一套可恢复状态契约，宿主扩展不会改变审批和执行边界。代价是需要自研有限状态机、MCP 握手和迁移器，并为 Host Hook 与内部事件分别维护测试。

## 验证

- 状态模型、G0-G4、迁移历史和配置安全格必须有单元测试。
- Host Hook 名称必须通过插件验证；内部事件必须能在 SQLite 中重放。
- 无沙箱写入、同名 Skill 覆盖和未验证的 `codex exec` 必须被拒绝。
