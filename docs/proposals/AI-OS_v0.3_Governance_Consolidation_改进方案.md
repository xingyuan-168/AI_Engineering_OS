# AI-OS 工程治理规范改进方案

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.1","status":"draft","owner":"product-manager","requirement_refs":["GOV-001","DOC-001"]} -->

> 适用对象：Codex / AI Agent 工程治理系统  
> 文档状态：提案（proposals）——经逐项源码核对后，可执行决策已转入 ADR-0011～0014；本文保留原始提案与核对差异记录  
> 文档目的：针对当前 AI-OS 工程治理规范及其运行时实现，给出下一阶段的结构化整改方案  
> 文档目的：针对当前 AI-OS 工程治理规范及其运行时实现，给出下一阶段的结构化整改方案  
> 建议版本：AI-OS v0.3 — Governance Consolidation  
> 核心目标：**收敛治理模型、消除旁路、统一事实源、降低重复实现、提升 Codex 原生能力适配度**

---

# 1. 当前系统定位

当前 AI-OS 已经不再只是“一套给 Codex 使用的提示词或工程规范”，而是逐步演化成：

> **运行在 Codex 之上的 AI 软件工程治理控制平面（Engineering Governance Control Plane）**

目前它已经覆盖：

- Workflow 生命周期管理
- G0-G4 Gate 阶段门禁
- Project Profile
- 多角色 Agent / Skill
- Git / Worktree 管理
- Docker / OCI 环境治理
- Evidence 工程证据
- Approval 人工审批
- Release 发布闭环
- Engineering Memory
- SQLite 状态持久化
- Host Operation 外部副作用治理
- MCP 结构化工具
- Codex Plugin / Hooks
- 测试、审计、恢复机制

整体方向正确，但当前已经进入一个新的阶段：

> **主要矛盾已经不是“功能不足”，而是治理能力过多以后产生的重复、漂移、旁路和复杂度。**

下一阶段不建议继续增加新的 Agent、Skill、Gate 或 Workflow。

应优先完成一次：

# Governance Consolidation

即：

1. 统一治理事实源
2. 统一授权入口
3. 统一 Artifact 契约
4. 统一 Codex 执行适配层
5. 清理重复能力
6. 降低普通任务治理成本

---

# 2. 总体整改原则

后续 AI-OS 应遵循以下原则。

## 2.1 AI-OS 管治理，Codex 管执行

AI-OS 的核心职责：

- Workflow
- Policy
- Gate
- Evidence
- Audit
- Release
- Engineering Memory

Codex 原生能力负责：

- 文件修改
- Shell 执行
- Worktree
- Sandbox
- Subagent
- Skills
- Hooks
- MCP 调用
- Git 基础操作

AI-OS 不应重复实现 Codex 已经成熟提供的执行层能力。

---

## 2.2 唯一事实源原则

任何核心治理事实只能有一个 Source of Truth。

必须重点解决：

- Artifact 路径多套定义
- Frontend Design 多套文档模型
- Profile 与 Workflow 重复定义
- README 人工统计 Skill / Agent / Tool
- Project Layout 被写死在 Profile 中
- Gate 检查规则分散

目标：

> 每项治理事实只定义一次，其余内容全部引用或自动生成。

---

## 2.3 治理策略只能单调收紧

Profile、Role、Task 只能增加限制，不能悄悄降低全局治理要求。

推荐关系：

```text
Baseline Policy
    ↓
Project Policy
    ↓
Task Policy
    ↓
Role Policy
```

最终有效策略为各层约束合并后的最严格结果。

---

## 2.4 Agent 上下文最小化

AI-OS 内部可以复杂。

但单次 Codex Task 不应该看到整个治理体系。

最终应由系统生成一个精简的：

```text
Task Contract
```

仅包含：

- Goal
- Scope
- Allowed Paths
- Forbidden Paths
- Required Skills
- Required Evidence
- Required Tests
- Gate Requirements
- Completion Contract

---

# 3. P0：必须优先修复的问题

---

# P0-01：建立统一 Governance Authorization Kernel

## 当前问题

目前部分写操作经过 AI-OS MCP / Runtime 的 Policy 检查。

但 Codex 还可以通过以下原生入口修改项目：

- `apply_patch`
- Shell / Bash
- Git CLI
- 某些直接文件操作
- MCP 外部工具

因此目前存在：

> AI-OS 正门有授权检查，但 Codex 原生执行路径可能形成旁路。

例如：

```text
AI-OS MCP 修改受保护路径
    ↓
Policy
    ↓
DENY
```

但：

```text
Codex apply_patch
    ↓
当前 Hook
    ↓
未完整执行统一 path policy
    ↓
可能写入
```

## 风险

严重级别：**P0**

可能导致：

- 绕过 protected paths
- 绕过 role scope
- 绕过 approval gate
- 绕过 task scope
- 绕过 project policy
- 治理记录与真实文件状态不一致

---

## 改造目标

建立一个唯一授权核心：

```text
Governance Authorization Kernel
```

所有写入类操作都必须调用它。

建议接口：

```python
authorize(
    session_id,
    workflow_id,
    task_id,
    role,
    tool,
    operation,
    paths,
    command,
    metadata,
) -> AuthorizationDecision
```

返回值：

```text
ALLOW
ASK
DENY
```

---

## 所有入口统一接入

```text
MCP
CLI
apply_patch
Shell
Git
Agent Tool
Hook
Host Operation
        ↓
Authorization Kernel
```

---

## 建议内部判断顺序

```text
1. Session 是否有效
2. Workflow 是否允许执行
3. Task 是否存在
4. 当前 Role 是否匹配
5. Operation 是否允许
6. Path 是否在 allowed scope
7. Path 是否命中 protected scope
8. 是否需要 Approval
9. 是否存在环境限制
10. 是否存在 Git / Release 特殊规则
11. 输出 ALLOW / ASK / DENY
```

---

## 验收标准

必须增加自动测试：

### Case A

通过 MCP 修改 protected path：

```text
DENY
```

### Case B

通过 apply_patch 修改同一路径：

```text
DENY
```

### Case C

通过 Shell 重定向写入：

```bash
echo test > protected/file
```

结果：

```text
DENY
```

### Case D

当前 Task 不允许修改 frontend：

```text
apply_patch frontend/*
```

结果：

```text
DENY
```

### Case E

获得批准后：

```text
ASK → Approval → ALLOW
```

---

# P0-02：Hook 必须升级为真实治理入口

## 当前问题

当前 Hook 主要偏向：

- Regex 检查危险命令
- 拦截 force push
- 拦截 reset --hard
- 拦截 rm
- 拦截 host pip install
- 拦截 docker volume prune

这些规则有价值。

但它更像：

> Dangerous Command Filter

而不是：

> Governance Enforcement Point

---

## 改造方案

PreToolUse Hook 不应再自己维护一套独立规则。

应将：

- tool name
- arguments
- target path
- command
- current task
- current role

传入 Authorization Kernel。

例如：

```text
PreToolUse
    ↓
parse operation
    ↓
Authorization Kernel
    ↓
ALLOW / ASK / DENY
```

---

## 特别处理

### apply_patch

必须解析：

- 新增文件
- 修改文件
- 删除文件
- Rename
- 多文件 patch

并取得完整 target path 列表。

---

### Shell

需要识别：

- >
- >>
- tee
- cp
- mv
- rm
- sed -i
- git checkout
- git restore
- git reset
- git clean
- package install
- docker volume
- release commands

不能只依靠字符串黑名单。

---

## 验收标准

同一个受限操作，无论通过：

- MCP
- apply_patch
- shell
- git

得到的授权结果必须一致。

---

# P0-03：建立统一 Artifact Catalog

## 当前问题

目前 Artifact 定义出现多套模型。

最典型的是前端设计文档。

一套使用：

```text
docs/design/UX_RESEARCH.md
docs/design/USER_FLOW.md
docs/design/WIREFRAME.md
docs/design/UI_SPEC.md
```

另一套 Workflow / Profile 使用：

```text
docs/PRODUCT_DESIGN.md
docs/INTERACTION_DESIGN.md
docs/UI_DESIGN.md
```

结果形成：

```text
Initializer 产生 A
Workflow 等待 B
Gate 检查 B
README 告诉 Agent 使用 A
```

这是典型的：

# Contract Drift

---

## 风险

- Codex 无法判断哪套文档是权威
- 重复生成
- Gate 假失败
- 文档长期漂移
- Profile / Workflow 互相冲突
- 增加模型上下文负担

---

## 改造目标

建立唯一：

```text
artifact-catalog.yaml
```

建议结构：

```yaml
artifacts:

  requirements:
    canonical_path: docs/requirements/REQUIREMENTS.md
    phase: requirements
    gate: G1
    producer: product
    reviewers:
      - reviewer

  product_design:
    canonical_path: docs/design/PRODUCT_DESIGN.md
    phase: design
    gate: G2
    producer: product
    reviewers:
      - reviewer

  interaction_design:
    canonical_path: docs/design/INTERACTION_DESIGN.md
    phase: design
    gate: G2
    producer: ux
    reviewers:
      - reviewer

  ui_design:
    canonical_path: docs/design/UI_DESIGN.md
    phase: design
    gate: G2
    producer: ui
    reviewers:
      - reviewer
```

---

## 所有系统统一引用 Catalog

禁止以下位置 hardcode Artifact Path：

- Workflow
- Gate
- Profile
- Skill
- Project Initializer
- README
- Tests
- Agent Persona

统一：

```text
Artifact Catalog
        ↓
Workflow
Gate
Project Init
Docs
Skills
Validators
```

---

## 验收标准

执行：

```text
artifact validate
```

必须检查：

- canonical artifact 是否唯一
- 是否存在重复 ID
- 是否存在重复路径
- Gate 引用是否有效
- Workflow 引用是否有效
- Producer 是否存在
- Reviewer 是否存在

---

# P0-04：清理 Frontend Design 双模型

## 改造原则

前端设计阶段只保留一套标准。

推荐：

```text
docs/design/
    PRODUCT_DESIGN.md
    INTERACTION_DESIGN.md
    UI_DESIGN.md
```

如仍需要：

```text
UX_RESEARCH
USER_FLOW
WIREFRAME
```

应作为上述 Artifact 的子阶段或者可选附属 Artifact，而不是另一套平行标准。

例如：

```yaml
ui_design:
  required:
    - PRODUCT_DESIGN
    - INTERACTION_DESIGN
    - UI_DESIGN

  optional:
    - UX_RESEARCH
    - USER_FLOW
    - WIREFRAME
```

---

# P0-05：Project Profile 禁止硬编码 AI-OS 自身目录

## 当前问题

当前通用 Profile 中出现类似：

```text
src/codex_ai_os/application/**
src/codex_ai_os/infrastructure/**
src/codex_ai_os/adapters/**
src/codex_ai_os/domain/**
```

Frontend 也存在类似：

```text
src/codex_ai_os/frontend/**
tests/frontend/**
```

这意味着：

> 名义上的 generic profile 实际绑定了 AI-OS 自身项目结构。

---

## 风险

无法正常应用于：

- FastAPI
- Django
- React
- Vue
- Next.js
- Node.js
- Go
- Rust
- Java
- Electron
- Monorepo

---

## 改造方案

拆分两层。

### Generic Profile

```text
profiles/
    generic/
        backend.yaml
        frontend.yaml
        fullstack.yaml
        cli.yaml
        library.yaml
```

Generic Profile 只定义：

- 需要哪些 artifact
- 需要哪些 checks
- 需要哪些 reviewers
- 哪些风险等级
- 哪些 gate

禁止定义固定源码目录。

---

### Project Layout

项目初始化时生成：

```text
.codex-os/project-layout.yaml
```

例如：

```yaml
project:
  type: fullstack

layout:
  frontend:
    source:
      - apps/web/**
    tests:
      - apps/web/tests/**

  backend:
    source:
      - apps/api/**
    tests:
      - apps/api/tests/**

  shared:
    - packages/shared/**
```

---

## Task Scope

由 Profile + Project Layout 动态计算：

```text
Profile
   +
Project Layout
   +
Task
   ↓
Allowed Paths
```

---

## 验收标准

用以下不同项目 Fixture 测试：

- Python Backend
- React Frontend
- Node Fullstack
- Go CLI
- Monorepo

同一 Profile 不得依赖：

```text
src/codex_ai_os/**
```

---

# P0-06：Release Package 必须改为白名单构建

## 当前问题

当前项目压缩包中混入大量运行态文件，例如：

```text
.codex-os/state/state.db
state backup
logs
executions
artifacts
.pytest_cache
.ruff_cache
__pycache__
coverage
```

---

## 风险

- 泄露本机运行历史
- 泄露绝对路径
- Package 膨胀
- 无法判断哪些是源码
- 用户获得脏状态
- 状态数据库可能污染首次运行

---

## 改造方案

禁止：

```text
zip current-working-directory
```

改为：

```text
Git tracked source
        ↓
Build
        ↓
Package allowlist
        ↓
Secret Scan
        ↓
Content Validator
        ↓
dist/
```

---

## 推荐 Package Manifest

```yaml
include:
  - src/**
  - profiles/**
  - workflows/**
  - skills/**
  - agents/**
  - docs/**
  - tests/**
  - README.md
  - AGENTS.md
  - pyproject.toml

exclude:
  - .git/**
  - .codex-os/state/**
  - logs/**
  - artifacts/**
  - .pytest_cache/**
  - .ruff_cache/**
  - __pycache__/**
  - coverage/**
```

---

## 发布前检查

必须自动检查：

```text
no sqlite state
no local logs
no pycache
no cache
no temporary artifacts
no secret
no local absolute path
```

---

# 4. P1：高优先级结构优化

---

# P1-01：建立 Codex Native Capability Adapter

## 当前问题

AI-OS 已自行实现或管理：

- Worktree
- Sandbox
- Agent
- Approval
- Hook
- Git Operation

而 Codex 自身也在提供类似能力。

可能出现多个状态源：

```text
Codex State
AI-OS State
Git State
SQLite State
```

之后需要大量 reconcile。

---

## 改造目标

AI-OS 不应与 Codex 原生能力竞争。

建立：

```text
Execution Capability Adapter
```

结构：

```text
AI-OS
    ↓
Capability Adapter
    ↓
Codex Native
    ↓
Git / Sandbox / Worktree / Agent
```

---

## Capability Adapter 建议接口

```python
create_worktree()
remove_worktree()
run_agent()
run_command()
apply_patch()
request_approval()
inspect_sandbox()
git_status()
commit()
push()
```

---

## 后端实现可替换

```text
CodexAdapter
LocalGitAdapter
TestAdapter
MockAdapter
```

未来 Codex API 变化时只改 Adapter。

---

# P1-02：引入 Assurance Level

## 当前问题

当前治理整体偏重。

对于不同任务，如果始终要求相同证据：

```text
pytest
ruff
pyright
security
dependency audit
SBOM
OCI
release verification
```

那么：

> 修改 README 和修改 Auth 系统会使用近似成本。

这是不合理的。

---

## 改造方案

引入：

# Assurance Level

---

## L0

适用：

- README
- 文档
- 注释
- 格式调整

要求：

```text
diff validation
basic lint
```

---

## L1

适用：

- 小型 Bug
- 非关键代码调整

要求：

```text
focused tests
lint
review
```

---

## L2

适用：

- 普通 Feature

要求：

```text
requirements
design
unit/integration tests
review
G1-G3
```

---

## L3

适用：

- Auth
- Permission
- Database
- Payment
- Security
- Infra

要求：

```text
full tests
security scan
dependency audit
migration verification
strong review
G0-G4
```

---

## L4

适用：

- Production Release
- Critical infrastructure
- High-risk deployment

要求：

```text
full G0-G4
SBOM
OCI
release candidate
rollback evidence
security review
artifact hash
```

---

## Risk Engine

建议：

```text
Task
  ↓
Risk Classification
  ↓
Minimum Assurance Level
```

原则：

> 用户可以主动提高 Assurance Level，但 Agent 不能降低系统确定的最低等级。

---

# P1-03：建立 Context Compiler

## 当前问题

随着：

- 21+ Skills
- 8+ Agents
- AGENTS
- Workflow
- Gate
- Profile
- Policy

不断增长，Agent 上下文会越来越复杂。

风险：

- 规则冲突
- 模型遗漏
- Token 浪费
- 模型过度保守
- 模型不知道当前任务真正需要什么

---

## 改造方案

增加：

```text
Context Compiler
```

根据：

- Project Profile
- Workflow Phase
- Current Task
- Role
- Risk Level
- Artifact
- Policy

生成：

```text
Task Contract
```

---

## Task Contract 示例

```markdown
# Task Contract

## Goal
修复登录接口 token refresh bug。

## Allowed Paths
- src/auth/**
- tests/auth/**

## Forbidden Paths
- infrastructure/**
- profiles/**
- workflows/**

## Required Evidence
- unit tests
- auth integration test
- reviewer approval

## Required Skill
- backend
- security-review

## Completion
- tests pass
- commit created
- evidence registered
```

Codex 执行任务时只看到这份契约和必要 Skill。

---

# P1-04：Git Push 改为 Checkpoint Policy

## 当前问题

当前规则倾向：

> 每个逻辑修改完成后立即 commit + push。

Commit 强制是合理的。

但 Push 强制过于频繁。

---

## 风险

- 网络异常阻塞 Agent
- Remote commit 过多
- 临时实验污染远端
- 长任务产生大量 push

---

## 建议

Commit：

```text
强制
```

Push：

```text
Checkpoint
```

可配置：

```yaml
git:
  push_policy: checkpoint
```

支持：

```text
immediate
checkpoint
release-only
```

---

## 推荐 checkpoint

必须 Push：

```text
task_complete
handoff
gate_transition
release_candidate
final_release
```

---

# P1-05：README / Inventory 自动生成

## 当前问题

出现类似：

```text
README 写 19 Skills
实际存在 21 Skills
```

这说明系统规模已经不适合人工维护数字和清单。

---

## 改造方案

新增：

```text
aios inventory build
```

自动扫描：

```text
skills/
agents/
profiles/
workflows/
mcp/
```

生成：

```text
.generated/inventory.json
docs/generated/INVENTORY.md
```

README 引用生成结果。

---

## 自动检查

CI 中执行：

```text
inventory generate
git diff --exit-code
```

如果文档未同步：

```text
FAIL
```

---

# 5. P1：治理模型进一步收敛

---

# P1-06：建立唯一 Governance Schema

建议把目前分散的：

- Workflow
- Gate
- Artifact
- Profile
- Role
- Approval
- Check
- Evidence

建立统一 Schema。

例如：

```text
governance-schema/
    artifact.schema.json
    workflow.schema.json
    profile.schema.json
    gate.schema.json
    task.schema.json
    evidence.schema.json
```

所有 YAML 在加载时必须 Validate。

---

# P1-07：将 Gate 从硬编码逻辑改成数据驱动

目标：

```yaml
gate: G3

requires:
  artifacts:
    - implementation
  checks:
    - unit-tests
    - lint
  reviews:
    - code-review
  evidence:
    - git-commit
```

Gate Engine 负责计算。

不要在 Python 逻辑里重复写：

```text
if G3 and frontend...
if backend...
if profile...
```

---

# P1-08：治理 Policy 与执行 Command 分离

建议明确区分：

```text
Policy Layer
Execution Layer
```

Policy：

```text
Can I do it?
```

Execution：

```text
How do I do it?
```

禁止在 Policy 内直接执行 Shell。

禁止在 Executor 内自行做治理决策。

---

# P1-09：统一 Engineering Evidence 模型

推荐 Evidence 基础结构：

```yaml
evidence:
  id:
  type:
  task_id:
  workflow_id:
  commit_sha:
  producer:
  generated_at:
  source:
  hash:
  metadata:
```

Evidence 类型建议统一：

```text
test_result
lint_result
security_result
review_result
git_commit
artifact
build_result
release_result
approval
```

---

# P1-10：禁止“自然语言完成状态”

Agent 不能仅通过：

```text
任务完成
测试通过
已经修复
```

改变 Workflow 状态。

任何阶段完成必须来自：

```text
Evidence + Gate Evaluation
```

而不是模型自报状态。

---

# 6. P2：中期能力增强

---

# P2-01：治理审计导出

新增：

```text
aios audit export
```

输出：

```text
audit.json
audit.md
```

包含：

- Workflow
- Task
- Agent
- Approval
- Evidence
- Git SHA
- Artifact Hash
- Release
- Security Check

用于：

- 项目交付
- Debug
- Compliance
- Review

---

# P2-02：治理审计导入

支持：

```text
aios audit import
```

用于迁移项目或恢复工程上下文。

---

# P2-03：Workflow / Gate 可视化

建议生成：

```text
Current Phase
Current Gate
Blocked Reason
Missing Evidence
Pending Approval
Active Task
```

重点不是做复杂 Dashboard。

先提供 CLI：

```text
aios status
```

---

# P2-04：治理指标

可以统计：

```text
Gate Failure Rate
Average Task Retry
Approval Delay
Test Failure Rate
Agent Rework Rate
Policy Deny Rate
Evidence Missing Rate
Rollback Rate
```

这些指标用于以后调优治理，而不是一味增加规则。

---

# P2-05：Policy Calibration

根据历史数据判断：

- 哪些检查经常没有价值
- 哪些 Gate 经常造成假阻塞
- 哪些 Task Scope 经常过宽
- 哪些 Rule 经常被触发
- 哪些 Assurance Level 过高或过低

---

# 7. 推荐的新架构

建议最终收敛为 6 个核心模块。

```text
                    AI-OS
                      │
       ┌──────────────┼──────────────┐
       │              │              │
   Workflow        Policy        Evidence
       │              │              │
       └──────── Gate Engine ─────────┘
                      │
            Engineering Knowledge
                      │
                 Release
                      │
             Execution Adapter
                      │
                   Codex
                      │
            Git / OCI / Worktree
```

---

# 8. 六个模块职责

## 8.1 Workflow

回答：

> 当前应该做什么？

负责：

- Project Lifecycle
- Task Lifecycle
- Phase Transition

---

## 8.2 Policy

回答：

> 当前允许做什么？

负责：

- Role Permission
- Path Permission
- Tool Permission
- Operation Permission
- Approval Requirement

---

## 8.3 Gate

回答：

> 现在能不能进入下一阶段？

负责：

- Requirements
- Checks
- Artifact
- Review
- Approval

---

## 8.4 Evidence

回答：

> 如何证明任务真的完成？

负责：

- Test Result
- Git SHA
- Build Result
- Artifact Hash
- Review
- Security Result

---

## 8.5 Engineering Knowledge

回答：

> 哪些工程事实需要长期保留？

只保存：

- Decision
- Reason
- Result
- Commit
- Artifact
- Confidence
- Source

禁止存：

- 原始聊天
- 大量日志
- 临时思考过程

---

## 8.6 Execution Adapter

回答：

> 如何让 Codex 真正执行？

负责连接：

- Codex
- Git
- Docker
- Worktree
- Shell
- File Operations

---

# 9. 下一版本建议

建议版本命名：

# AI-OS v0.3 Governance Consolidation

这一版本原则上不增加新的业务能力。

---

# 10. v0.3 必须完成的范围

## Phase 1 — Authorization Consolidation

完成：

- Governance Authorization Kernel
- MCP 接入
- Hook 接入
- apply_patch 接入
- Shell 接入
- Git 接入
- 测试旁路

---

## Phase 2 — Contract Consolidation

完成：

- Artifact Catalog
- Frontend Design 单一模型
- Governance Schema
- Profile 去硬编码
- Project Layout

---

## Phase 3 — Execution Consolidation

完成：

- Codex Capability Adapter
- Worktree Adapter
- Sandbox Adapter
- Agent Adapter
- Git Adapter

删除重复实现或标记 deprecated。

---

## Phase 4 — Governance Cost Reduction

完成：

- Risk Engine
- Assurance Level
- Context Compiler
- Task Contract

---

## Phase 5 — Delivery Hygiene

完成：

- Package allowlist
- state/cache/log cleanup
- inventory generator
- README auto-generation
- package validator

---

# 11. v0.3 验收标准

全部满足才算完成。

---

## Authorization

- MCP 和 apply_patch 的权限结果一致
- Shell 无法绕过 protected path
- Git 无法绕过 release rule
- 未授权 Task 无法修改 scope 外文件

---

## Artifact

- 每个 Artifact 只有一个 canonical path
- Workflow 不 hardcode path
- Gate 不 hardcode path
- Profile 不 hardcode path

---

## Profile

Generic Profile 不包含：

```text
src/codex_ai_os/**
```

---

## Codex Integration

AI-OS 不直接重复维护 Codex 已提供能力，统一通过 Adapter。

---

## Risk Governance

README 修改不会触发完整 Production Governance。

Auth / DB / Release 修改会自动提升 Assurance Level。

---

## Context

普通 Task 只注入相关：

- Role
- Policy
- Skill
- Evidence
- Gate

不加载全量 20+ Skill。

---

## Release

发布包不得包含：

```text
state.db
logs
cache
coverage
__pycache__
local artifacts
```

---

## Documentation

以下信息必须自动生成：

- Skill List
- Agent List
- MCP Tool List
- Workflow List
- Profile List

---

# 12. 明确暂时不要做的事情

v0.3 阶段建议禁止继续增加：

- 新 Agent
- 新 Skill
- 新 Gate
- 新 Workflow
- 可视化大前端
- 多模型智能路由
- 云端控制台
- 新数据库
- 向量数据库
- 更复杂的 Memory

除非这些能力是完成上述 P0/P1 整改所必需。

---

# 13. Codex 执行总原则

可以直接把下面原则写入整改任务入口：

```text
本阶段目标不是增加 AI-OS 功能，而是收敛现有治理体系。

任何修改必须优先减少：
1. 重复事实源
2. 重复执行能力
3. 重复规则
4. 治理旁路
5. Agent 上下文复杂度

如果新实现会引入第二套：
- Policy
- Artifact Definition
- Worktree Manager
- Sandbox Manager
- Agent State
- Path Rule
则默认认为设计错误，除非有明确 ADR 说明原因。
```

---

# 14. 推荐优先级总表

| ID | 优先级 | 项目 |
|---|---|---|
| P0-01 | P0 | Governance Authorization Kernel |
| P0-02 | P0 | Hook 统一授权 |
| P0-03 | P0 | Artifact Catalog |
| P0-04 | P0 | Frontend Design 单模型 |
| P0-05 | P0 | Profile 去 AI-OS 路径硬编码 |
| P0-06 | P0 | Release 白名单打包 |
| P1-01 | P1 | Codex Capability Adapter |
| P1-02 | P1 | Assurance Level |
| P1-03 | P1 | Context Compiler |
| P1-04 | P1 | Git Checkpoint Push |
| P1-05 | P1 | Inventory 自动生成 |
| P1-06 | P1 | Governance Schema |
| P1-07 | P1 | 数据驱动 Gate |
| P1-08 | P1 | Policy / Execution 分层 |
| P1-09 | P1 | Evidence Schema |
| P1-10 | P1 | 禁止自然语言完成状态 |
| P2-01 | P2 | Audit Export |
| P2-02 | P2 | Audit Import |
| P2-03 | P2 | CLI Status |
| P2-04 | P2 | Governance Metrics |
| P2-05 | P2 | Policy Calibration |

---

# 15. 最终目标

AI-OS 下一阶段不应该继续向“大而全 Agent 框架”发展。

推荐长期定位：

> **Codex Engineering Governance Control Plane**

Codex 负责：

```text
执行
```

AI-OS 负责：

```text
为什么执行
是否允许执行
什么时候执行
执行到什么程度
怎样证明执行成功
是否允许进入下一阶段
哪些工程知识需要长期保留
```

最终形成：

```text
Codex = Execution Engine

AI-OS = Engineering Governance System
```

这将比同时维护第二套：

- Worktree
- Sandbox
- Agent Runtime
- Git Runtime

更加稳定，也更容易适配未来 Codex 的原生能力变化。

---

# 16. 下一步建议

建议下一次直接让 Codex 执行：

# AI-OS v0.3 Governance Consolidation

执行顺序必须固定：

```text
1. 修 Authorization
2. 修 Artifact Contract
3. 修 Profile
4. 修 Codex Adapter
5. 加 Assurance Level
6. 加 Context Compiler
7. 清 Release Package
8. 自动生成 Inventory
9. 最后再做 Audit / Metrics
```

其中：

> 在 P0 全部完成以前，不允许新增新的业务治理功能。

---

**文档结束**
