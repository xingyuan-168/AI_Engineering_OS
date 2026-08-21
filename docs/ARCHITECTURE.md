# 系统架构

## 分层

```text
Codex Host
       |
Codex Plugin / CLI Adapter
       |
Local CLI Runtime
       |
AI Project Manager ---- Routing Decision
       |
Workflow Engine ---- SQLite state/events
       |
Skill + Agent Router ---- Agent Handoff
       |
Execution Manager ---- Git / Worktree / Test tools
       |
Docs + ADR + CHANGELOG ---- Memory Manager / FTS5 index
```

## 边界

- Project Manager 只负责理解目标、复杂度和流程选择，不直接写业务代码。
- Workflow Engine 只负责状态和调度，不替代领域文档。
- Agent 只修改被授权的 Worktree 路径，并通过产物交付。
- Execution Manager 是所有命令和文件写入的统一控制点。
- Markdown/Git 保存事实；SQLite 保存状态和索引，不成为不可审计的第二事实来源。
- Codex Host 负责模型、Session 和 Tool Runtime；Plugin 只能通过本地 CLI Runtime 调用 OS 能力。
- Worktree 是 Agent 写入隔离边界；交接包是 Agent 状态转换的前置证据。
- 模块职责、权限归属和冲突处理以 [BOUNDARY_SPEC.md](BOUNDARY_SPEC.md) 为事实来源。

## 关键数据流

1. 目标进入 Project Manager，生成项目清单、待确认问题和可解释路由评分。
2. Workflow Engine 校验路由结果，创建状态和任务事件；冲突或边界分数进入人工确认。
3. Skill/Agent 读取相关文档，在授权 Worktree 产出文档、代码或验证结果。
4. Agent 通过结构化 Handoff 交付产物、Commit、hash、测试和风险，消费者验收后才允许状态转换。
5. Execution Manager 执行受控操作并记录日志，Gate 汇总证据推进或暂停状态。
6. Release 产出候选物，Memory Manager 将 ADR、CHANGELOG、失败经验和路由决策写入可审计索引。

## 可替换性

Workflow 运行时、CLI、SQLite、Memory 检索和宿主适配均通过接口隔离；替换实现不得改变文档格式、任务事件、交接、审批和安全语义。

## Codex 与本地运行时边界

```text
Codex Host
  -> Plugin / Project .codex/
  -> Codex Adapter
  -> Local CLI Runtime
  -> Workflow / Approval / Document / Agent
  -> Execution Manager
  -> Docker/Podman Sandbox
```

Codex Host 提供推理和对话上下文；Local CLI Runtime 是状态和权限事实来源。宿主不得直接绕过 CLI 写项目文件或执行高风险命令。

## Memory 与 SQLite

Markdown/Git、ADR 和 CHANGELOG 是事实来源；SQLite 保存 Workflow、Task、Handoff、Worktree、Plugin Run、Routing Decision 和 Memory 元数据。`memory_records` 保存经过校验的记录，FTS5 只提供检索索引；来源 hash 不匹配时记录必须进入复核，不能继续作为事实。

## 配置加载

内置安全默认值 -> 全局 Plugin -> 项目 `.codex/` -> `project.yaml`/`.codex-os/` -> CLI 显式参数。安全字段只允许收紧，不能由项目配置放宽。

## 关键数据流

目标和配置进入 Project Manager，Workflow Engine 创建状态和任务；Agent 通过 Codex Adapter 获取推理；Execution Manager 在 Docker/Podman 中执行；事件写入 SQLite，事实写入 Markdown/Git，发布候选物汇总所有证据。

详细契约见 [RUNTIME_SPEC.md](RUNTIME_SPEC.md)、[CONFIG_SPEC.md](CONFIG_SPEC.md)、[WORKFLOW_SPEC.md](WORKFLOW_SPEC.md)、[WORKTREE_SPEC.md](WORKTREE_SPEC.md)、[AGENT_HANDOFF.md](AGENT_HANDOFF.md)、[PLUGIN_SPEC.md](PLUGIN_SPEC.md)、[BOUNDARY_SPEC.md](BOUNDARY_SPEC.md)、[EXECUTION_POLICY.md](EXECUTION_POLICY.md) 和 [OBSERVABILITY.md](OBSERVABILITY.md)。
