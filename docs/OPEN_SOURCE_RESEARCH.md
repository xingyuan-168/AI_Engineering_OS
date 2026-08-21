# 开源项目核心提取与复用决策

版本：V2.0-derived
状态：仓库级研究基线已完成；包级核验门禁已定义
原则：先拆解能力，再研究项目；先确认 License 和边界，再决定复用方式。

## 1. 研究输出要求

每个候选项目必须形成一条可审计记录，至少包含：

- 项目名称、官方地址、核验日期、固定版本或 commit。
- License、依赖、运行要求和安全公告状态。
- 解决的问题和核心抽象。
- 关键流程、数据结构、扩展点和失败处理方式。
- 可直接使用的部分、只能借鉴的部分和明确不能复用的部分。
- 与 Codex AI Engineering OS 的集成边界。
- 结论：直接使用、二次开发、提取设计思想或自研。
- 对应的 `TECH_STACK.md` 条目和 ADR。

本文件已完成官方仓库级核验。发布包、传递依赖和部署镜像仍按第 5 节门禁逐项核验；在核验完成前，任何候选项目均不得进入 V1 核心依赖。

## 1.1 官方来源核验记录（2026-08-20）

| 项目 | 官方仓库 | 默认分支 | 最新 release/tag | 仓库级 License | V1 结论 |
| --- | --- | --- | --- | --- | --- |
| LangGraph | [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | `main` | `sdk==0.4.3` | MIT | 提取状态图和检查点思想，不作为 V1 核心依赖 |
| CrewAI | [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | `main` | `1.15.17` | MIT | 提取角色/任务模型，不作为 V1 流程控制器 |
| MetaGPT | [FoundationAgents/MetaGPT](https://github.com/FoundationAgents/MetaGPT) | `main` | `v0.8.1` | MIT | 提取 SOP 和产物驱动流程 |
| AutoGen | [microsoft/autogen](https://github.com/microsoft/autogen) | `main` | `python-v0.7.5` | CC-BY-4.0（仓库 API 标识） | 仅作通信机制研究；不直接引入，发布前需核验包级 License |
| OpenHands | [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) | `main` | `v1.14.0` | MIT | 提取执行循环和隔离边界，不作为 V1 执行核心 |
| Mem0 | [mem0ai/mem0](https://github.com/mem0ai/mem0) | `main` | `ts-v3.1.6` | Apache-2.0 | 提取记忆模型，V1 使用自有 Markdown + SQLite |
| Zep | [getzep/zep](https://github.com/getzep/zep) | `main` | `zep-ingest-v0.2.1` | Apache-2.0 | 提取记忆生命周期，V1 不引入服务依赖 |
| Penpot | [penpot/penpot](https://github.com/penpot/penpot) | `develop` | `2.17.1` | MPL-2.0 | 作为外部设计工具候选，不进入 OS 核心运行时 |
| Excalidraw | [excalidraw/excalidraw](https://github.com/excalidraw/excalidraw) | `master` | `v0.18.1` | MIT | 作为原型工具候选，不进入 OS 核心运行时 |

核验范围：GitHub 官方仓库元数据、默认分支、最新 release/tag 和仓库级 SPDX 标识。核验日期：2026-08-20。上述 release/tag 是研究快照，不代表 V1 依赖版本。

## 2. 按能力板块提取核心

| 能力板块 | 候选项目 | 必须提取的核心 | V1 决策 |
| --- | --- | --- | --- |
| Workflow 状态管理 | LangGraph | 图状态、节点转换、检查点、暂停/恢复、状态持久化 | 提取状态图和检查点思想；V1 使用自有 Python 状态机 |
| Agent 角色协作 | CrewAI | Role、Task、Crew、委派、顺序/层级流程 | 提取角色和任务模型；V1 使用自有 Agent 适配层 |
| 软件团队流程 | MetaGPT | 角色 SOP、产物驱动、阶段门禁、团队协作顺序 | 提取流程模板和产物约束，不直接引入完整运行时 |
| Agent 通信 | AutoGen | 消息协议、群聊、路由、终止条件、人工介入 | 提取通信协议思想；V1 统一为事件和任务消息 |
| 代码执行环境 | OpenHands | 工具调用循环、工作区抽象、运行时隔离、补丁执行 | 提取执行边界；V1 写操作和高风险执行强制使用默认沙箱 |
| 长期记忆 | Mem0/Zep 类项目及相关实现 | 记忆写入、检索、压缩、来源、失效和权限 | 提取记忆生命周期思想；V1 使用 Markdown + SQLite FTS |
| 原型和设计协作 | Penpot / Excalidraw | 画布模型、组件资产、设计 Token、原型交付和导出 | 作为设计流程工具和参考，不作为 OS 核心运行时 |

## 3. 各项目核心提取说明

### LangGraph

重点研究有向状态图、节点输入输出、条件转换、checkpoint、interrupt/resume 和失败恢复。落地时将 Workflow 状态与项目文档、任务事件和人工审批绑定，避免只保存模型上下文而无法审计。

### CrewAI

重点研究角色、任务、团队、委派和流程模式。落地时保留“角色负责什么、任务产出什么、谁可以委派”的清晰模型，但不让多个 Agent 框架同时控制同一状态机。

### MetaGPT

重点研究产品、架构、开发、测试等角色如何通过标准产物衔接。落地时把文档和评审门禁作为接口，而不是依赖隐式对话记忆。

### AutoGen

重点研究 Agent 间消息格式、路由、群聊编排和停止条件。落地时统一采用带 `task_id`、`from`、`to`、`type`、`payload`、`artifacts` 和 `approval_required` 的任务事件。

### OpenHands

重点研究代码执行、工具调用、工作区和运行时隔离。落地时所有命令必须经过权限策略，文件写入必须受允许路径限制，生产和破坏性操作必须人工确认。

### Memory 类项目

重点研究事实记忆、事件记忆、语义检索、记忆压缩、来源追踪和失效策略。V1 先保证“可读、可审计、可删除”，不因引入向量检索而牺牲事实来源。

### Penpot / Excalidraw

重点研究 User Flow、线框、组件、设计 Token 和设计资产如何进入前端实现。落地时将设计文档作为前端编码前的门禁输入，并保留链接、导出文件和版本信息。

## 4. 复用决策矩阵

| 结论 | 适用条件 | 必须留下的记录 |
| --- | --- | --- |
| 直接使用 | License、运行稳定性、隔离和扩展能力均满足要求 | 版本、依赖、配置、升级责任和安全扫描 |
| 二次开发 | 核心能力成熟，但需要适配本地工作流或安全策略 | Fork/补丁边界、上游同步策略和差异说明 |
| 提取设计思想 | 机制有价值，但引入运行时会造成重复或复杂度 | 核心抽象、保留理由和自有实现边界 |
| 自研 | 无法满足安全、许可证、可控性或本地运行要求 | 调研证据、放弃候选、实现范围和 ADR |

## 5. 研究门禁

在外部项目进入 `TECH_STACK.md` 的“采用”部分前，必须完成：

1. 官方仓库、版本、License 和依赖核验。
2. 关键 API 或核心代码的最小验证。
3. 运行、升级、故障和安全风险评估。
4. 与现有 Workflow、Agent、Execution Manager 的边界确认。
5. 通过 ADR 记录采用或不采用的结论。

## 6. 当前冻结结论

V1 使用自有 Workflow/Agent/Execution 接口，采用 LangGraph 的状态图和检查点设计思想但不引入其运行时；CrewAI、MetaGPT、AutoGen 和 OpenHands 仅作为能力与流程参考；Memory 使用可审计的 Markdown + SQLite 方案；Penpot/Excalidraw 作为 UI 项目设计工具。任何改变上述结论的行为必须新增 ADR 并完成包级 License、依赖和安全核验。

## 7. 研究完成定义

每个能力板块均已具备候选项目、核心提取、官方来源、仓库级 License、版本快照、V1 采用方式、集成边界和风险门禁；缺少其中任一项时，研究任务状态为 `blocked`，不得转入技术实现。
