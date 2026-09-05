# ADR-0001：MVP 运行时与状态存储

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"approved","owner":"architect","requirement_refs":["REQ-1.6.2","GOV-001"]} -->

- 状态：Accepted
- 日期：2026-08-20
- 范围：Workflow、Agent、Execution 和 Memory 的 V1 实现
- 后续决策：第 2 条“优先评估 LangGraph 作为实现依赖”已由 [ADR-0002](ADR-0002-v1-runtime-contract-convergence.md) 取代；本文件其余决策继续有效。

## 背景

系统需要支持项目流程编排、状态管理、暂停恢复、Agent 协作、命令执行和长期知识沉淀。首期目标是个人本地可运行和可审计，而不是立即建设多人 SaaS 平台。

## 决策

1. 使用 Python 作为 V1 主运行时，使用 Pydantic 定义配置和事件模型。
2. 使用 LangGraph 风格的状态图作为 Workflow 设计基线，优先评估 LangGraph 作为实现依赖。
3. 使用 SQLite 保存 Workflow 检查点、任务、审批和事件索引。
4. 使用 Markdown + Git 保存项目事实、ADR、CHANGELOG、失败方案和发布记录。
5. Agent 角色、消息协议和执行权限由本项目定义，通过适配层接入外部框架。
6. V1 使用 Docker/Podman 作为代码、测试和高风险命令的默认沙箱；容器不可用时仅允许低风险只读诊断，高风险任务必须阻塞，不得静默降级到宿主机。

## 未采用的替代方案

- 不同时引入 CrewAI、AutoGen 和 LangGraph 作为并行流程控制器，避免状态和重试语义分裂。
- 不在 V1 直接引入向量数据库，先保证记忆的事实来源、可读性和可删除性。
- 不直接把 OpenHands 作为执行核心；执行隔离统一由本项目的 Docker/Podman 策略、Worktree 路径边界和 Execution Manager 管理。

## 后果

正面结果是本地部署、状态恢复、文档 Review 和执行隔离边界清晰，依赖可替换。代价是首期需要检查 Docker/Podman 前置条件并自行实现 Agent 适配、命令策略和部分编排逻辑；升级外部框架时必须维护适配层和事件契约。

## 验证与演进动作

- 在开源研究门禁中锁定 LangGraph、Pydantic、SQLite 相关版本和许可证。
- 为状态转换、恢复、路径权限和人工审批补充集成测试。
- 试点结束后根据失败率、可观测性和执行隔离效果决定是否引入独立 Memory 服务或额外 Harness 适配器；不改变 V1 默认沙箱和权限边界。
