# AI Engineering OS 0.2.0 开源研究与复用决策

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"review-ready","owner":"architect","requirement_refs":["REQ-1.6.2","GOV-001","REPO-001","AGENT-001","EXEC-001","MEMORY-001"]} -->

核验日期：2026-08-24

状态：G2 研究证据就绪；新增依赖与执行镜像仍受逐项安全门禁约束。

原则：先拆解能力，再研究项目；先确认版本、License、来源与边界，再决定采用、参考或拒绝。

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

本文件已完成官方仓库、官方文档、PyPI 锁定包元数据和本地镜像 digest 核验。发布包、传递依赖和执行镜像仍按第 5 节门禁逐项扫描；在核验完成前，任何参考候选均不得进入 `0.2.0` 核心依赖。

## 1.1 0.2.0 已采用运行依赖快照

下表版本来自 `uv.lock` 与 `uv tree --depth 1`，License 来自安装包元数据与上游官方页面。`uv.lock` 必须纳入 Git，并通过 `uv lock --check` 保证解析未漂移。

| 组件 | 锁定版本 | License | 决策与边界 | 官方来源 |
| --- | --- | --- | --- | --- |
| Python | 3.12 系列；当前开发环境 3.12.13 | PSF-2.0 | 采用；`0.2.0` 保持 `>=3.12,<3.13` | [Python 3.12.13](https://www.python.org/downloads/release/python-31213/) |
| MCP Python SDK | 2.0.0 | MIT | 采用；只暴露确定性 CLI/MCP 用例，不承担模型推理 | [PyPI mcp 2.0.0](https://pypi.org/project/mcp/2.0.0/) |
| Pydantic | 2.13.4 | MIT | 采用；配置、任务、证据与接口严格校验 | [PyPI pydantic](https://pypi.org/project/pydantic/2.13.4/) |
| platformdirs | 4.11.3 | MIT | 采用；只用于受控平台目录解析 | [PyPI platformdirs](https://pypi.org/project/platformdirs/) |
| PyYAML | 6.0.3 | MIT | 采用；仅通过安全加载器读取受管配置 | [PyPI PyYAML](https://pypi.org/project/PyYAML/) |
| Typer | 0.27.1 | MIT | 采用；提供本地 CLI，所有写用例仍进入应用服务 | [PyPI Typer](https://pypi.org/project/typer/) |
| pytest / pytest-cov | 9.1.1 / 7.1.0 | MIT | 开发采用；生成质量与覆盖率证据 | [pytest](https://pypi.org/project/pytest/) / [pytest-cov](https://pypi.org/project/pytest-cov/) |
| Ruff / Pyright | 0.16.4 / 1.1.411 | MIT | 开发采用；静态检查结果必须绑定来源 Commit | [Ruff](https://docs.astral.sh/ruff/) / [Pyright](https://github.com/microsoft/pyright) |
| pip-audit | 2.10.1 | Apache-2.0 | 开发采用；扫描锁定依赖并保存机器报告 | [pypa/pip-audit](https://github.com/pypa/pip-audit) |
| FastAPI / HTTPX | 0.141.1 / 0.28.1 | MIT / BSD-3-Clause | 仅 ERP 试点开发依赖，不进入 OS Web 控制面 | [FastAPI](https://pypi.org/project/fastapi/0.141.1/) / [HTTPX](https://pypi.org/project/httpx/0.28.1/) |

## 1.2 治理机制的官方行为核验

| 机制 | 官方事实 | 0.2.0 采用方式 | 风险控制 |
| --- | --- | --- | --- |
| Git Worktree | 同一仓库可建立多个 linked worktree，每个 Worktree 有独立 HEAD/index，Refs 仍共享 | 采用，任务与集成 Worktree 均由 Runtime 创建和登记 | 使用 `git worktree list --porcelain` 校验归属；清理前检查 dirty、Review 与 merge；不直接操作 `$GIT_DIR/worktrees` |
| GitHub PR / protected branch | Protected branch 可要求 Review、状态检查并默认禁止 force push；PR merge 可保留显式 merge point | 采用，G4 以 PR 连接集成分支与 `main` | Runtime 复核 head/base/merge Commit；不依赖管理员绕过权限 |
| SQLite FTS5 | FTS5 是 SQLite 的全文检索虚拟表模块，支持列过滤、前缀和布尔查询 | 采用，自有 Memory 表保存事实元数据，FTS5 只作项目隔离索引 | 启动时探测 FTS5；触发器与迁移保证索引一致；Secret 检查先于索引 |
| Podman/Docker OCI | Podman `run` 支持 `--network none`、`--read-only`、PID/资源和 capability 限制 | 采用，ExecutionService 统一生成锁定参数 | 镜像必须使用完整 digest；非 root、只读根、断网、cap-drop 和挂载边界由真实 OCI 测试验证 |
| uv lock | `uv.lock` 保存跨平台精确解析，`--locked`/`uv lock --check` 可拒绝漂移 | 采用，版本与锁文件 hash 写入 Release Manifest | 不手工编辑 lock；变更依赖必须单独 Review、审计和生成 SBOM |

官方来源：[Git Worktree](https://git-scm.com/docs/git-worktree)、[GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)、[GitHub PR merge](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/configuring-commit-merging-for-pull-requests)、[SQLite FTS5](https://www.sqlite.org/fts5.html)、[Podman run](https://docs.podman.io/en/latest/markdown/podman-run.1.html)、[uv project layout](https://docs.astral.sh/uv/concepts/projects/layout/)。

## 1.3 执行镜像核验与阻塞风险

当前仓库锁定 `python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2`。本地 `podman image inspect` 证明该 digest 对应 Python 3.12.13、Debian bookworm-slim 和上游构建 revision `3362634339580d3232e65a66dd5a36c47ae7ff14`；Docker Hub 也将相同 digest 标记为 3.12.13 官方镜像。

同时，Python 3.12.13 已被 3.12.14 安全修复版本取代，Docker Hub 已提供 `3.12.14-slim-bookworm`，并且仓库当前 digest 的 Hub 扫描页面仍报告高危/严重漏洞。结论是：旧 digest 可用于重现既有隔离测试，但不能仅凭“官方镜像”身份通过 G3/G4 安全门禁。G2 必须选择并锁定新的完整 digest，G3 必须保存镜像扫描/SBOM 证据；若仍有超过策略阈值且无批准例外的漏洞，发布保持阻塞。

来源：[Python 3.12.13 发布说明](https://www.python.org/downloads/release/python-31213/)、[Python 官方镜像](https://hub.docker.com/_/python/)、[当前 digest 页面](https://hub.docker.com/layers/library/python/3.12.13-slim-bookworm/images/sha256-4181eb0137487812d8b84ee677de85e5c9f7e08b7c3e16fc564b077cff4907a8)、[Docker Library Python](https://github.com/docker-library/python)。

## 1.4 参考项目历史快照（2026-08-20）

| 项目 | 官方仓库 | 默认分支 | 研究时 release/tag | 仓库级 License | 0.2.0 结论 |
| --- | --- | --- | --- | --- | --- |
| LangGraph | [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | `main` | `sdk==0.4.3` | MIT | 提取状态图和检查点思想，不作为 0.2.0 核心依赖 |
| CrewAI | [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | `main` | `1.15.17` | MIT | 提取角色/任务模型，不作为 0.2.0 流程控制器 |
| MetaGPT | [FoundationAgents/MetaGPT](https://github.com/FoundationAgents/MetaGPT) | `main` | `v0.8.1` | MIT | 提取 SOP 和产物驱动流程 |
| AutoGen | [microsoft/autogen](https://github.com/microsoft/autogen) | `main` | `python-v0.7.5` | 代码 MIT；文档 CC-BY-4.0 | 仅作通信机制研究；不直接引入，若复用内容需区分代码与文档 License |
| OpenHands | [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) | `main` | `v1.14.0` | MIT | 提取执行循环和隔离边界，不作为 0.2.0 执行核心 |
| Mem0 | [mem0ai/mem0](https://github.com/mem0ai/mem0) | `main` | `ts-v3.1.6` | Apache-2.0 | 提取记忆模型，0.2.0 使用自有 Markdown + SQLite |
| Zep | [getzep/zep](https://github.com/getzep/zep) | `main` | `zep-ingest-v0.2.1` | Apache-2.0 | 提取记忆生命周期，0.2.0 不引入服务依赖 |
| Penpot | [penpot/penpot](https://github.com/penpot/penpot) | `develop` | `2.17.1` | MPL-2.0 | 作为外部设计工具候选，不进入 OS 核心运行时 |
| Excalidraw | [excalidraw/excalidraw](https://github.com/excalidraw/excalidraw) | `master` | `v0.18.1` | MIT | 作为原型工具候选，不进入 OS 核心运行时 |

核验范围：GitHub 官方仓库元数据、默认分支、当时 release/tag 和仓库级 SPDX 标识。核验日期：2026-08-20。上述 release/tag 是历史研究快照，不代表 `0.2.0` 依赖版本，也不会被自动升级为依赖。

## 2. 按能力板块提取核心

| 能力板块 | 候选项目 | 必须提取的核心 | 0.2.0 决策 |
| --- | --- | --- | --- |
| Workflow 状态管理 | LangGraph | 图状态、节点转换、检查点、暂停/恢复、状态持久化 | 提取状态图和检查点思想；0.2.0 使用自有 Python 状态机 |
| Agent 角色协作 | CrewAI | Role、Task、Crew、委派、顺序/层级流程 | 提取角色和任务模型；0.2.0 使用自有 Agent 适配层 |
| 软件团队流程 | MetaGPT | 角色 SOP、产物驱动、阶段门禁、团队协作顺序 | 提取流程模板和产物约束，不直接引入完整运行时 |
| Agent 通信 | AutoGen | 消息协议、群聊、路由、终止条件、人工介入 | 提取通信协议思想；0.2.0 统一为事件和任务消息 |
| 代码执行环境 | OpenHands | 工具调用循环、工作区抽象、运行时隔离、补丁执行 | 提取执行边界；0.2.0 写操作和高风险执行强制使用默认沙箱 |
| 长期记忆 | Mem0/Zep 类项目及相关实现 | 记忆写入、检索、压缩、来源、失效和权限 | 提取记忆生命周期思想；0.2.0 使用 Markdown + SQLite FTS5 |
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

重点研究事实记忆、事件记忆、语义检索、记忆压缩、来源追踪和失效策略。0.2.0 先保证“可读、可审计、可删除”，不因引入向量检索而牺牲事实来源。

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

## 6. 0.2.0 冻结结论

`0.2.0` 使用自有 Workflow/Agent/Execution 接口，采用 LangGraph 的状态图和检查点设计思想但不引入其运行时；CrewAI、MetaGPT、AutoGen 和 OpenHands 仅作为能力与流程参考；Memory 使用可审计的 Markdown + SQLite/FTS5；Penpot/Excalidraw 仅作为 UI 项目外部工具。任何改变上述结论的行为必须新增 ADR 并完成包级 License、依赖、安全和迁移核验。

## 7. 研究完成定义

每个能力板块均已具备候选项目、核心提取、官方来源、仓库级 License、版本快照、`0.2.0` 采用方式、集成边界和风险门禁；已采用依赖具备锁定版本与 License 记录。执行镜像升级与安全扫描是 G2 设计输入和 G3 强制检查，不得被历史 Podman 成功记录替代。
