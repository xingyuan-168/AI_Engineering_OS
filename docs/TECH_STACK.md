# Codex AI Engineering OS 技术栈

版本：V2.0-derived
状态：可执行文档基线
选型原则：本地可运行、可审计、可恢复、可替换、最小运行时依赖。

## 1. 选型总览

| 层次 | V1 建议 | 选择理由 |
| --- | --- | --- |
| 主运行时 | Python 3.12 | AI/自动化生态成熟，适合 CLI、文件处理和测试 |
| 包管理 | `pyproject.toml` + uv | 可复现环境和快速安装 |
| Workflow | 自有 Python 状态机，采用 LangGraph 的图状态/检查点思想 | V1 保持状态、审批和执行契约可控，不引入第二套运行时 |
| 数据模型 | Pydantic v2 | 统一校验项目、任务、事件和配置 |
| CLI | Typer | 适合个人本地工作流和脚本化执行 |
| API（可选） | FastAPI | 为未来桌面/团队控制面保留接口，不作为 MVP 必需入口 |
| 状态日志 | SQLite | 保存 Workflow、任务、审批和事件，单机部署成本低 |
| 事实文档 | Markdown + Git | 人可读、可 Review、可追踪、易迁移 |
| Memory 检索 | SQLite FTS5 + 文档索引 | V1 可审计，避免过早引入向量数据库 |
| 协作隔离 | Git Branch + Worktree | 每个 Agent 独立工作区，减少写入冲突 |
| 执行隔离 | Docker/Podman 默认沙箱；宿主机仅执行低风险诊断 | V1 默认隔离，沙箱不可用时高风险任务阻塞 |
| 测试 | pytest | 单元、集成和 Workflow 场景测试 |
| 质量检查 | Ruff、Pyright、Markdown/YAML lint | 保持代码和治理文件一致 |
| 安全检查 | Gitleaks、pip-audit、Bandit/Semgrep | 凭据、依赖和代码安全基线 |
| 发布 | Git tag + 发布候选物 | 禁止复制目录式版本升级 |

## 2. 运行时模块

### AI Project Manager

输入业务目标和项目上下文，输出项目类型、复杂度、风险级别、所选 Workflow、初始文档任务和待确认问题。

### Workflow Engine

负责状态图、转换条件、检查点、暂停、恢复、重试和事件日志。任何状态转换必须具备前置条件和产物证据。

### Skill System

每个 Skill 使用 `SKILL.md` 描述：名称、目标、输入、输出、工具权限、禁止事项、完成条件和失败处理。

### Agent System

每个 Agent 使用独立配置描述角色、任务范围、允许路径、分支、输入产物、输出产物和 Review 要求。

### Execution Manager

统一执行命令、读写文件、运行测试和生成补丁。执行前检查命令风险、工作目录和允许路径；破坏性命令不得自动执行。

### Memory System

将 ADR、CHANGELOG、失败记录、技术选型和发布记录写入 Git；SQLite 只保存索引、事件和检索元数据，不替代事实文档。

## 3. 核心接口

### 项目清单

```yaml
project_id: codex-ai-engineering-os
name: Codex AI Engineering OS
spec_version: V2.0
project_type: personal-local-os
risk_level: medium
active_workflow: new-project
status: planning
source_of_truth: docs/
approval_policy: critical-gates-human
```

### Workflow 定义

```yaml
name: new-project
states: [intake, requirements, research, design, implementation, verify, release, memory]
transitions:
  - from: intake
    to: requirements
    requires: [goal, scope, acceptance_criteria]
    approval: human
retry_policy: bounded
checkpoint: sqlite
```

### 任务事件

```yaml
task_id: TASK-001
workflow_id: WF-001
from: project-manager
to: architect
type: artifact-request
payload: {}
artifacts: []
approval_required: false
```

## 4. 技术选型边界

- V1 不同时引入 LangGraph、CrewAI、AutoGen 等多个流程运行时。
- Agent 角色协议由本项目定义，外部框架只通过适配层接入。
- 所有开源依赖必须记录固定版本、License、升级责任和安全扫描结果。
- 生产发布不由本地 Execution Manager 无条件完成。
- 未来替换 SQLite、CLI 或 Workflow 运行时时，不改变 Markdown 文档和事件接口。

## 5. 环境与交付

建议提供以下命令：

```text
codex-os init
codex-os run new-project --goal "开发一个 ERP 采购模块"
codex-os status
codex-os resume <workflow-id>
codex-os check-docs
codex-os research <capability>
codex-os verify
codex-os release --candidate
```

## 6. 版本与依赖冻结规则

- Python：3.12.x，补丁版本由 `uv.lock` 冻结。
- Pydantic：2.x；Typer：0.12+；pytest：8.x；Ruff：0.6+；Pyright：1.1+。
- SQLite：随 Python 运行环境提供，启用 WAL、外键约束和 FTS5。
- V1 不引入 LangGraph、CrewAI、MetaGPT、AutoGen、OpenHands、Mem0、Zep、Penpot 或 Excalidraw 作为核心运行时依赖；对应能力采用自有接口或外部设计工具接入。
- 每次新增依赖必须同时更新本文件、许可证清单、锁文件、SBOM 和一条 ADR（如属于重大决策）。

## 7. 技术栈完成定义

本文件已确定 V1 运行时、状态存储、文档事实源、CLI、测试、安全工具、版本策略和外部项目边界。实施阶段只允许在既定边界内锁定补丁版本，不得把候选开源项目直接加入依赖而跳过研究记录和安全门禁。

## 8. 产品运行边界

- Codex 宿主负责模型推理、Skill 触发和 Agent 对话；本地 CLI 不重复实现模型 API 客户端。
- 本地 CLI 负责配置校验、Workflow、审批、事件、沙箱、文件策略、SQLite 和发布候选物。
- Plugin 的 `skills/` 提供默认 Skill；仓库新增项目专属 Skill 使用 `.agents/skills/`。提示词、模板和允许路径的运行时覆盖放在 `.codex-os/`，同名 Skill 不合并。
- Windows 是 V1 唯一支持平台；核心接口保持平台无关，但不承诺 Linux/macOS 安装验收。
- Docker/Podman 是代码修改、测试和高风险命令的默认执行环境；不可用时不自动降级到宿主机。
