# AI Engineering OS — Governance Core 轻量化大重构实施规格

> **重构性质**：允许大改、删除、合并模块；如现有实现与目标明显冲突，可推翻该模块重写。  
> **禁止事项**：禁止通过复制 `src_v2/`、`project_v2/`、`new/`、`backup/` 等目录保留旧实现。旧实现由 Git 历史保存。  
> **核心原则**：**Git 保存历史，工作树只保存当前正确实现。**

---

# 0. 最终目标：只解决真正需要解决的七件事

AI Engineering OS 的最终定位不是另一个 Agent 平台、Coding Runtime、CI/CD 平台或软件供应链审计系统。

它只需要成为 **Codex 的工程治理层（Codex Engineering Governance Layer）**。

AI Engineering OS 必须告诉 Codex：

1. **正式开发前必须有 GitHub 仓库。**
2. **需求拆分后必须先查找可直接使用、可二开的开源项目，并记录复用决策。**
3. **禁止复制 `v1/v2/backup/final/new/old` 等源码副本做版本迭代；仓库必须保持干净。**
4. **项目文档必须维护，且只维护真正受本次变更影响的文档。**
5. **复杂任务使用 Codex 原生子 Agent；需要并行开发时用 Git Worktree 隔离。**
6. **涉及前端/UI/交互时，先产出可审核的交互原型和 UI 方案，用户确认后再编码。**
7. **任务结束后删除无复用价值的一次性脚本、缓存、调试文件和临时产物；这些内容不得进入 Git。**

此外保留一个轻量支撑能力：

8. **项目长期记忆**：只沉淀架构决策、技术选型、Bug 根因、失败经验和可复用工程经验；不保存聊天、过程日志和无价值审计流水。

除此之外的功能，必须先证明对以上目标有直接价值，否则默认不进入 Core。

---

# 1. 最重要的架构边界

## 1.1 Codex 是专家执行者

Codex 原生负责：

- 理解仓库
- 自主分析技术问题
- 制定具体编码策略
- 文件搜索和编辑
- Shell/PowerShell
- Git 操作
- 调试
- 构建
- 测试
- 重构
- 使用自身工具
- 使用自身 Plan Mode
- 使用自身子 Agent 能力

AI Engineering OS **不得重新实现或接管这些能力**。

## 1.2 AI Engineering OS 是制度，不是“第二个程序员”

AI Engineering OS 只负责：

- 规则
- 前置条件
- 项目事实和上下文
- 生命周期门禁
- GitHub/仓库卫生
- 文档影响检查
- 前端人工确认
- Codex 子 Agent + Worktree 的工程隔离规范
- Memory
- 完成检查

正确关系：

```text
用户
  ↓
Codex
  ↓  自动加载/遵守
AI Engineering OS Governance
  ↓
项目仓库
```

不是：

```text
用户
  ↓
AIOS Planner
  ↓
AIOS Agent Runtime
  ↓
AIOS Tool Runtime
  ↓
AIOS Execution Manager
  ↓
Codex
```

## 1.3 核心设计守则

> **AIOS 约束“能否做、何时做、做完要留下什么”，不约束 Codex “具体怎么把专业工作做出来”。**

---

# 2. 当前源码审计结论

本次审计直接基于提供的 `AI-OS(1).zip`。

当前代码不是空壳，相反已经明显**过度工程化**。

## 2.1 当前规模

当前源码约：

- `src/codex_ai_os/`：约 **25,347 行 Python**
- `tests/`：约 **13,389 行 Python**
- 测试文件：约 **56 个**
- 文档：约 **61 个 Markdown**
- 文档总量：约 **7,653 行**
- MCP Tools：约 **30 个**
- Plugin Skills：约 **21 个**
- SQLite migration：`0001` ~ `0008`
- `verification.py` 默认检查：**17 项**

这些数字本身不是错误；问题是，大量复杂度用于解决的并不是用户的七个核心痛点。

## 2.2 当前最明显的功能膨胀

以下模块已经明显超出“Codex 工程治理层”的必要范围：

- 完整 Release Candidate 构建和发布事务
- G4 GitHub Release 自动发布
- SBOM 生成
- 镜像 SBOM
- Trivy/Image 扫描式供应链验证
- Verification Cache
- Wheelhouse/离线依赖审计快照
- Host Operation 租约、幂等、副作用对账
- 大量 Evidence 对象、Hash、上下文绑定
- 每阶段强制全部产物
- 完整 OCI-first 执行路径
- 自有多 Agent 任务 DAG / 调度模型
- 自有 Tool/Execution authority 倾向
- Artifact Catalog 和强制全部文档契约
- 复杂配置 Schema/文档版本/Artifact 版本联动
- 过重的发布/审计/恢复状态机

这些能力不是绝对“无价值”，但**不应该成为默认 Core，更不应该限制 Codex 原生工程行为**。

---

# 3. 源码中已经确认的主要跑偏点

## 3.1 `SessionStart` 把 AIOS 描述成“执行权威”

当前文件：

```text
plugins/ai-engineering-os/hooks/session_start.py
```

当前注入内容包含：

```text
Use the MCP workflow state as the execution authority
```

这会把 AIOS 从“治理层”推向“执行控制器”。

### 必须修改

SessionStart 只做三件事：

1. 告诉 Codex 当前项目启用了 AI Engineering OS。
2. 提醒读取 `AGENTS.md` 和项目事实文档。
3. 提醒执行当前任务适用的治理前置检查。

禁止再声明：

- MCP 是 Codex 的 execution authority
- Codex 必须把所有工程行为交给 AIOS 执行
- 所有工具必须走 AIOS Runtime

建议语义：

```text
This project uses AI Engineering OS governance.
Keep Codex's native engineering workflow.
Before repository-changing work:
- read AGENTS.md and relevant docs;
- verify GitHub/repository readiness;
- complete required open-source research;
- honor frontend approval and worktree rules when applicable;
- finish with tests, document impact review, memory update, and repository cleanup.
```

---

## 3.2 `PreToolUse` 过度限制 Codex 正常工程能力

当前文件：

```text
plugins/ai-engineering-os/hooks/pre_tool_use.py
```

当前不仅阻止危险命令，还阻止：

- `pip install`
- `pip wheel`
- `npm install`
- `npm add`
- `npm build`
- `pnpm`
- `yarn`
- `cargo build`
- `sed -i`

并强制：

```text
Project dependency installation and builds must run through the governed OCI environment.
```

### 这是本次必须大改的 P0 项

保留拦截：

- force push
- `git reset --hard`（在可能破坏用户未提交修改时）
- 强制 `git clean`
- 大范围递归删除
- 删除远端分支
- 直接破坏 `.git`
- 越权写 `input/`
- 复制式版本目录/文件
- 未满足前置 Gate 时向正式源码写入

取消对以下正常 Codex 行为的全局禁止：

- pip/npm/pnpm/yarn/cargo 正常安装
- 正常 build
- 正常测试
- 正常局部文件编辑
- Codex 自己选择的工程工具

### 新原则

Hook 只保护：

```text
Safety + Governance Boundary
```

不接管：

```text
Engineering Execution Strategy
```

---

# 4. 当前 Verification 系统必须做减法

当前：

```text
src/codex_ai_os/application/verification.py
```

默认运行 17 项：

1. pytest
2. ruff
3. pyright
4. docs
5. secret-scan
6. bandit
7. plugin-validator
8. skill-validator
9. agent-validator
10. hook-fixture
11. mcp-contract
12. build-install
13. dependency-audit
14. security-scan
15. real-oci
16. image-sbom
17. image-scan

其中第一项已经运行完整 pytest，随后又重新运行多个 pytest 子集以形成独立“证据”。

这是典型的“为了证明验证而重复验证”。

## 4.1 新的默认 Verification

默认日常任务最多保留：

```text
1. 任务相关测试（targeted tests）
2. Ruff / 项目自身 lint
3. Git diff --check
4. Repository Hygiene Check
5. 必要时 Pyright/type check
```

Secret 检查可以保留为提交前安全兜底，但不要构建一整套 Evidence Runtime。

## 4.2 不再默认运行

以下全部移出默认 Core：

- Bandit 全仓扫描
- dependency-audit
- SBOM
- image-sbom
- image-scan
- real OCI 证明
- build-install 证明
- 重复 Plugin/Skill/Agent pytest
- Verification Cache
- wheelhouse 离线审计

## 4.3 严格模式

如未来确有生产安全需求，可单独提供：

```text
assurance: strict
```

但 strict 是**可选 Profile**，不得成为普通项目默认路径。

strict 可包含：

- dependency audit
- security scan
- SBOM
- image scan
- Release checklist

Core 不依赖 strict 存在。

---

# 5. Release / G4 系统应从 Core 移除

当前重型模块：

```text
src/codex_ai_os/application/release.py          ~1018 行
src/codex_ai_os/application/g4.py               ~732 行
src/codex_ai_os/application/verification_cache.py ~820 行
src/codex_ai_os/infrastructure/operations.py    ~586 行
```

这些代码处理：

- Release Candidate
- GitHub Release
- Tag
- Assets
- SBOM
- Checksum
- 回滚包
- 外部副作用幂等
- Host Operation lease
- 远端对账
- Release manifest
- draft release
- reconcile

## 5.1 重构决定

**从默认 AI Engineering OS Core 删除。**

用户需要版本管理，但版本管理的目标是：

```text
GitHub + Branch + Commit + Tag（用户需要发布时）
```

不是让 AIOS 自建完整 Release 平台。

## 5.2 保留的最小版本规则

AIOS 只检查：

- 当前仓库有 GitHub remote
- 当前开发在合理 branch/worktree
- 不通过复制目录管理版本
- 任务结束形成有效 commit
- 重要里程碑由 Codex/用户决定是否 tag
- 不自动 force push
- 不自动发布 GitHub Release

GitHub Release 自动化不是 Core 需求。

---

# 6. Host Operation / Evidence 系统应大幅删除

当前存在：

```text
application/environment_operations.py
application/g4.py
application/release.py
application/verification_cache.py
infrastructure/operations.py
infrastructure/evidence.py
infrastructure/executions.py
domain/operations.py
domain/artifacts.py
```

以及数据库中：

- host_operations
- operation_attempts
- operation_resources
- operation_authorizations
- evidence_contexts
- check_evidence
- review_evidence
- gate_evidence_bundle
- release_records
- release_artifacts
- repository_audits / findings 的大量持久化字段

这些是企业审计/分布式可靠操作系统的做法。

## 新目标

AIOS 只需要知道：

```text
当前项目是否允许进入编码
当前任务是否完成必要前置条件
当前 Worktree 是否干净
前端是否已获得用户确认
任务结束是否满足质量/文档/清理规则
Memory 是否需要写入
```

不需要记录每一次命令的：

- lease
- generation
- request_hash
- policy_hash
- input_hash
- command_hash
- 远端操作重放
- 外部副作用重对账

## 修改要求

能删除就删除。

不为了向后兼容继续保留复杂系统。

Git 本身已经是项目版本和变更证据。

---

# 7. Workflow 必须从“大状态机”收敛为轻量生命周期

当前核心：

```text
application/workflow.py               ~2581 行
infrastructure/workflows.py           ~1400 行
domain/workflow.py                     ~335 行
```

当前承担：

- G0-G4
- task
- task group
- evidence
- release
- host operation
- approvals
- integration branch
- handoff
- coordination
- recovery
- migration revalidation
- release closure

这已经远超最终需求。

## 7.1 新生命周期

只保留：

```text
PREPARE
  ↓
DESIGN
  ↓
IMPLEMENT
  ↓
VERIFY
  ↓
DONE
```

状态只用于判断“是否满足治理前置条件”，不指导 Codex 如何执行专业工作。

## 7.2 五阶段语义

### PREPARE

检查：

- GitHub repository ready
- `input/` 不可修改
- requirements/scope 已明确
- 开源调研已完成

通过后允许进入设计或编码。

### DESIGN

仅适用于：

- 架构影响明显
- API/DB 设计变更
- 前端/UI/交互

前端项目必须完成：

- 交互原型
- UI 方案
- 用户确认

普通小 Bug 可以跳过 DESIGN。

### IMPLEMENT

Codex 自主完成。

AIOS 不接管：

- 文件修改算法
- build
- debugging
- dependency installation
- testing strategy

### VERIFY

检查：

- 任务相关测试
- 代码质量基础项
- 受影响文档
- 仓库卫生
- 临时文件清理
- Memory

### DONE

要求：

- Git 工作树干净或状态明确
- 变更已提交
- 必要时 push
- 无未处理临时文件
- 无复制式源码版本
- 项目事实已同步

---

# 8. Gate 从 G0-G4 改为“少量硬规则”

当前 `gates/G0.yaml` ~ `G4.yaml` 强制大量文档和发布证据。

建议删除当前五层企业 Gate。

改为三类真实 Gate：

## Gate A — Code Start Gate

**阻止正式源码写入。**

条件：

- GitHub remote 存在且可用
- 仓库不是脏乱状态
- 需求范围已明确
- 开源调研已完成
- 若项目已有事实文档，相关文档已加载

## Gate B — Frontend Approval Gate

仅当涉及 UI/交互时启用。

条件：

- 原型存在
- UI 方案存在
- 用户明确批准

未批准时：

- 可以修改设计稿/原型
- 不允许正式 frontend implementation

## Gate C — Finish Gate

条件：

- 任务相关测试通过
- 受影响文档已同步
- 仓库卫生通过
- 一次性文件已清理
- Memory 已写入或明确“不需要写入”
- 版本没有通过目录副本管理

不要为每一个阶段要求 SHA-256 Evidence Bundle。

---

# 9. 开源调研必须保留，但大幅简化

这是用户最核心的硬要求之一。

当前：

```text
docs/OPEN_SOURCE_RESEARCH.md
```

已经做得非常重：

- 版本
- License
- 安全公告
- 依赖
- SBOM
- 镜像风险
- 多个框架完整研究

## 9.1 新规则

每个**新需求/新能力**在编码前必须回答：

1. 有没有成熟开源项目可以直接用？
2. 有没有适合二开的项目？
3. 有没有项目中的某个模块/设计可以提取？
4. 为什么最终选择“直接用 / 二开 / 提取思想 / 自研”？

## 9.2 最小输出

```markdown
# Open Source Research

## Requirement
本次需求是什么。

## Candidates
### Project A
- URL:
- License:
- 解决什么：
- 可直接复用：
- 可二开：
- 值得学习：
- 风险：

## Decision
- use / fork / extract / build
- reason:
```

无需：

- 为每个候选生成供应链审计
- 为未采用项目生成 SBOM
- 固定几十个字段
- 引入新的 Agent Framework

## 9.3 开源复用总原则

**本轮轻量化重构不得引入 LangGraph、CrewAI、MetaGPT、OpenHands 等新的 Runtime 依赖。**

原因：

Codex 已经提供专业 Agent 能力。

这些项目最多作为设计参考，不能再次把 AIOS 扩张成 Agent 平台。

---

# 10. 文档治理必须保留，但从“61份文档”收敛

当前 `docs/` 约 61 份 Markdown。

问题不是文档多本身，而是：

- 大量文档长期同时处于活跃事实源
- 许多信息重复
- Codex 进入任务前阅读链过长
- 维护成本高
- 容易产生规范冲突

当前 `docs/README.md` 要求 Codex 按很长的顺序读取：

```text
SCOPE
PROJECT_MASTER
BOUNDARY_SPEC
CONFIG_SPEC
WORKFLOW_ROUTING_RULES
WORKFLOW_SPEC
SKILL_SPEC
AGENT_SPEC
AGENT_HANDOFF
WORKTREE_SPEC
PLUGIN_SPEC
EXECUTION_POLICY
领域文档
TEST_PLAN
...
```

这已经违背“给专家提供清晰制度而不是让专家先读一套教材”的目标。

## 10.1 AIOS 自身目标活跃文档

重构后建议只长期保留：

```text
README.md
AGENTS.md

docs/
├── REQUIREMENTS.md
├── SCOPE.md
├── ARCHITECTURE.md
├── GOVERNANCE_RULES.md
├── OPEN_SOURCE_RESEARCH.md
├── MEMORY.md
├── WORKTREE.md
├── FRONTEND_GATE.md
├── TEST_PLAN.md
├── CHANGELOG.md
└── ADR/
    ├── README.md
    └── 少量仍然有效的重大决策
```

如果 API/SQLite 本身确实需要维护，再保留：

```text
API_SPEC.md
DATABASE.md
```

其他文档没有独立存在的必要时直接合并或删除。

## 10.2 必须从活跃树删除的历史/过重文档

优先删除：

```text
docs/proposals/
docs/RELEASE_CLOSURE_EVIDENCE.md
docs/RELEASE_CLOSURE_MATRIX.md
docs/V1_RELEASE_REPORT.md
docs/RELEASE_CHECKLIST.md   # 若默认不再做 release automation
docs/OBSERVABILITY.md       # 合并成 README/DEBUGGING 小节即可
docs/MIGRATION_SPEC.md      # 如重建轻量 DB，改为简短升级说明
docs/RUNTIME_SPEC.md        # 核心边界并入 ARCHITECTURE/GOVERNANCE_RULES
docs/PLUGIN_SPEC.md         # 若内容仅为插件内部架构，合并 ARCHITECTURE
docs/BOUNDARY_SPEC.md       # 关键边界并入 GOVERNANCE_RULES
docs/AGENT_HANDOFF.md       # 轻量 handoff 规则并入 WORKTREE
docs/AGENT_SPEC.md          # 角色由 .codex/agents 表达
docs/SKILL_SPEC.md          # Skill 本身即事实源
docs/WORKFLOW_ROUTING_RULES.md # 合并 GOVERNANCE_RULES
docs/EXECUTION_POLICY.md    # 移除全局 OCI 强制后大幅缩减
```

**不要移动到 `docs/archive/`。**

Git 历史就是 archive。

删除即可。

---

# 11. 项目文档模板也必须瘦身

当前 `templates/project_docs.py` 初始化大量文档：

- PROJECT_MASTER
- SCOPE
- PRODUCT_REQUIREMENTS
- USER_STORY
- BUSINESS_RULES
- ARCHITECTURE
- TECH_STACK
- SECURITY
- TEST_PLAN
- CHANGELOG
- ENVIRONMENT
- API
- DATABASE
- 多份 frontend design 文件
- ADR

对个人/小型项目太重。

## 11.1 新项目永远存在的最小文档

```text
README.md
AGENTS.md

input/
output/

docs/
├── REQUIREMENTS.md
├── SCOPE.md
├── ARCHITECTURE.md
├── OPEN_SOURCE_RESEARCH.md
├── CHANGELOG.md
└── ADR/
```

## 11.2 条件生成

只有真正需要时生成：

```text
docs/API_SPEC.md       # 有公开/内部 API 契约时
docs/DATABASE.md       # 有数据库时
docs/TEST_PLAN.md      # 非简单项目/有明确验收矩阵时
docs/SECURITY.md       # 有特殊安全边界时
docs/DEPLOYMENT.md     # 需要部署时

docs/design/
├── PROTOTYPE.html
└── UI_SPEC.md
```

## 11.3 `input/` 与 `output/` 是硬要求

当前源码中：

```text
application/repository.py
```

存在：

```text
_FORBIDDEN_LEGACY_DOC_TREES = ("docs/archive", "input")
```

这与用户原始要求直接冲突。

必须修改：

- `input/` 是合法且受保护的用户输入目录。
- AIOS/Codex 不允许修改、重命名、删除 `input/` 现有内容。
- `output/` 是最终用户交付物目录。
- `output/` 不用于保存源码副本、缓存、临时构建、测试日志。
- 是否将大型 `input/output` 二进制放入 Git，由项目 `.gitignore`/LFS 策略决定，但 AIOS 不得把 `input/` 视为垃圾目录。

---

# 12. Frontend/UI Gate 必须保留，但文档压缩成“可审核产物”

用户的目标不是写大量 UX 文档，而是：

> 在真正开发 UI 前先看见、确认界面和交互，以避免返工。

## 新的前端流程

```text
Requirement
  ↓
User Flow（可直接写入 REQUIREMENTS/UI_SPEC）
  ↓
Interactive HTML Prototype
  ↓
UI Spec
  ↓
User Approval
  ↓
Codex Frontend Implementation
```

## 默认只要求两个正式设计产物

```text
docs/design/PROTOTYPE.html
docs/design/UI_SPEC.md
```

复杂项目可选：

```text
USER_FLOW.md
DESIGN_TOKEN.md
COMPONENT_LIBRARY.md
```

不要强制所有前端项目同时维护：

- UX_RESEARCH
- USER_FLOW
- WIREFRAME
- PRODUCT_DESIGN
- INTERACTION_DESIGN
- UI_DESIGN
- UI_SPEC
- COMPONENT_LIBRARY
- DESIGN_TOKEN

除非项目真的需要。

---

# 13. Skill 体系必须删除“教 Codex 写代码”的 Skill

当前约 21 个 Skill。

## 13.1 保留

建议保留：

```text
requirement-analysis
open-source-research
architecture-design
api-design
database-design

product-design          # 可与 interaction/ui 合并
html-prototype
ui-design               # 可与 product-design 合并

testing
code-review
security-review         # 条件启用
memory-manager
agent-manager           # 改名/改职责为 subagent-worktree
```

## 13.2 删除

建议删除：

```text
backend-implementation
frontend-implementation
execution-manager
release-manager

new-project-orchestrator
feature-development-orchestrator
bug-fix-orchestrator
```

原因：

- Backend/Frontend implementation 是 Codex 的原生专业能力。
- Execution 是 Codex 的原生工具能力。
- Release automation 不再属于 Core。
- Codex 有 Plan Mode，不需要另造复杂 orchestrator 教它怎么编码。

## 13.3 新增/改名

将 `agent-manager` 收敛为：

```text
subagent-worktree
```

只描述：

- 什么时候值得调用 Codex 子 Agent
- 怎么拆工作树
- 怎么避免路径重叠
- 怎么 handoff
- 怎么 review/merge/cleanup

不实现新的 Agent Runtime。

---

# 14. Codex 子 Agent + Worktree：保留工程纪律，删除自有调度平台

用户所谓“多 Agent”明确是：

> **Codex 本身的子 Agent + Worktree**，不是 AIOS 创建一组独立模型进程。

因此当前自有的：

```text
application/coordination.py
infrastructure/coordination.py
domain/coordination.py
task_groups
task_dependencies
自有 scheduler / DAG
```

应大幅删减，能删则删。

## 新原则

Codex 主 Agent 自己负责：

- 判断是否需要子 Agent
- 技术拆分
- 调用子 Agent
- 综合结果

AIOS 只约束：

- 并行写任务使用独立 Worktree
- 写路径明显冲突的任务不能同时修改
- 子 Agent 不写主 Worktree
- 子 Agent 完成后回报变更、测试和风险
- 主 Agent/Reviewer 审核后合并
- 合并完成及时删除 Worktree
- 不保留孤儿 Worktree

不需要 AIOS 自己构建完整 DAG Runtime。

---

# 15. Worktree 逻辑应合并成一个轻量模块

目前分散：

```text
application/worktree.py
infrastructure/worktrees.py
adapters/worktree.py
application/coordination.py
infrastructure/coordination.py
```

建议收敛：

```text
src/codex_ai_os/worktree.py
```

或最多：

```text
application/worktree.py
adapters/git.py
```

提供少数能力：

```text
prepare_worktree(task)
check_worktree(task)
finish_worktree(task)
cleanup_worktree(task)
```

不要加入复杂 lease、generation、operation reconciliation。

---

# 16. Repository Governance 是当前真正值得保留的核心

当前：

```text
application/repository.py
```

是整个项目中最接近用户最终目标的模块之一。

保留并简化。

## 必须检查

### GitHub

正式编码前：

- Git repo 存在
- GitHub remote 存在
- remote 可访问
- 当前工作分支合理

**没有 GitHub：允许做需求分析/开源调研/项目文档；禁止向正式源码目录进入 IMPLEMENT。**

### 仓库卫生

禁止：

```text
src_v1/
src_v2/
project_backup/
project_final/
old/
backup/
copy/
final/
new/
temp/
tmp/
debug/
```

禁止文件：

```text
*_backup.*
*_old.*
*_final.*
*_v1.*
*_v2.*
fix_*_vN.*
*.bak
```

但要避免误伤真实语义中的版本文件，例如：

```text
api/v1/
migration/v2/
protocol/v1/
```

只有明显“复制源码保存历史”的目录才拦截。

### Git 污染

不得跟踪：

- `.venv/`
- `node_modules/`
- `__pycache__/`
- `.pytest_cache/`
- `.ruff_cache/`
- `.codex-os/state/`
- `.codex-os/logs/`
- 临时日志
- build/dist（除非明确是发布资产）
- Secret

---

# 17. 一次性文件：默认删除，而不是“审计留存”

用户要求非常明确：

> 临时测试脚本、临时文件、缓存等无复用价值的内容，任务结束直接删除，并且不要 Git。

新分类只需要两类：

## disposable

- 临时测试脚本
- debug 脚本
- 临时日志
- 缓存
- 一次性转换脚本
- build 中间物
- 临时截图
- AI 辅助分析文件

任务结束：

```text
DELETE
```

不得 Git。

## reusable

如果 Codex 判断脚本值得长期复用：

必须：

- 移到正式 `scripts/` 或 `tools/`
- 改成正式命名
- 有用途说明
- 与当前项目真实功能相关

否则删除。

### 删除现有复杂第三类默认审计模型

普通任务不需要把：

- 每一次测试 stdout
- execution log
- failure snapshot
- 每个 hash
- 每个 command evidence

永久保存。

失败现场只有在**当前任务仍 blocked 且确实需要继续调试**时临时保留；任务关闭后清理。

---

# 18. Memory 必须轻量化，但不能删除

Memory 是原始痛点之一。

当前：

```text
infrastructure/memory.py
```

可以保留，但状态机需要缩小。

## 只记

- decision
- bug
- lesson
- pattern

可选：

- project-summary

## 推荐状态

```text
active
superseded
invalid
```

不必为个人工程记忆维护：

```text
pending
needs_review
revoked
expired
deleted
...
```

除非确实有业务需求。

## 写入格式

每条 Memory 最少：

```yaml
type:
title:
summary:
source:
source_commit:
tags:
```

## 写入时机

只在：

- 重大决策完成
- Bug 找到根因并修复
- 失败方案值得以后避免
- 项目形成可复用经验

时写入。

不记录：

- 每次命令
- 每次 task 状态
- 聊天过程
- Codex 推理
- 测试 stdout
- 普通成功操作

---

# 19. `.codex-os/` 必须变成“运行状态目录”，而不是第二项目

当前 ZIP 中 `.codex-os/` 已出现大量：

- logs
- executions
- artifacts
- state.db
- 多个 DB backup
- test traceability
- gate YAML

这会造成项目膨胀。

## 重构后建议

```text
.codex-os/
├── project.yaml
├── context/
│   └── PROJECT_CONTEXT.md
└── state/
    └── state.db
```

可选：

```text
.codex-os/session/
```

但必须自动清理。

全部忽略 Git。

## 删除

```text
.codex-os/logs/
.codex-os/artifacts/
.codex-os/gates/
.codex-os/test-traceability.yaml
.codex-os/state/backups/   # 迁移成功后不长期累积
```

如果当前 DB 有珍贵 Memory：

先导出 Memory，再重建轻量 schema。

运行状态不是项目事实，不值得长期备份堆积。

---

# 20. SQLite Schema 建议直接重建轻量版

当前 `0001` ~ `0008` 已发展出：

- workflow_runs
- tasks
- events
- approvals
- artifacts
- documents
- handoffs
- executions
- worktrees
- repository_audits
- repository_findings
- file_lifecycle_entries
- routing_decisions
- task_groups
- task_dependencies
- evidence...
- host_operations...
- release...
- operation attempts/resources/authorization...
- FTS...

对于最终目标过重。

## 新 schema 建议只保留

```text
projects
tasks
approvals
worktrees
memories
```

可选：

```text
events
```

其中 events 只保留极少量重要状态，不作为完整审计日志。

## 迁移策略

不要为了兼容旧 Runtime 永久背负 8 代迁移。

允许：

1. 检查旧 DB 是否有 active Memory。
2. 导出有效 Memory。
3. 删除/重建 Runtime DB。
4. 导入有效 Memory。
5. 成功后删除临时迁移文件。
6. Git 历史保存旧 schema 代码。

---

# 21. Artifact Catalog 建议删除

当前：

```text
catalog/artifacts.yaml
application/artifact_catalog.py
domain/artifacts.py
```

把大量文档定义成强制 artifact，并参与 Gate、producer、reviewer、hash。

这对于最终目标过度。

替代方式：

在一个简短配置中定义：

```yaml
documents:
  always:
    - docs/REQUIREMENTS.md
    - docs/SCOPE.md
    - docs/ARCHITECTURE.md
    - docs/OPEN_SOURCE_RESEARCH.md
    - docs/CHANGELOG.md
  conditional:
    api: docs/API_SPEC.md
    database: docs/DATABASE.md
    frontend:
      - docs/design/PROTOTYPE.html
      - docs/design/UI_SPEC.md
```

足够。

---

# 22. MCP API 从 30 个缩减为不超过 10 个高层工具

当前 MCP 暴露内部实现细节：

- workflow_create
- workflow_begin
- workflow_step
- host_operation_execute
- host_operation_reconcile
- verification_prepare
- database_migrate
- release_candidate_create
- task_amend_evidence
- environment_prepare
- environment_verify
- ...

Codex 不应该需要理解 AIOS 内部状态机。

## 推荐 MCP

最多保留：

```text
project_init
project_check
context_refresh
task_start
task_status
approval_record
worktree_manage
memory_search
memory_record
task_finish
```

如果可以继续合并，优先更少。

例如：

`worktree_manage(action=prepare|finish|cleanup)`。

内部实现不要暴露成几十个 Tool。

---

# 23. CLI 也同步收敛

当前：

```text
src/codex_ai_os/cli/app.py ~1710 行
```

命令过多。

目标：

```text
codex-os init
codex-os check
codex-os status
codex-os memory
codex-os worktree
codex-os finish
codex-os mcp
```

调试命令可以内部使用，但不要成为核心公共接口。

删除默认：

- release-candidate
- host-operation
- verification-prepare
- environment-adopt/prepare/verify 一整套
- database-migrate 作为治理 CLI 的职责
- workflow create/begin/step 的过细接口

---

# 24. Docker/Podman 从“强制执行引擎”降为项目环境选项

Docker 的价值仍然存在：

- 避免物理机安装大量依赖
- 项目隔离
- 复现环境

但它不应该成为：

> Codex 所有 build/install/test 必须经 AIOS ExecutionService 的硬限制。

新规则：

- 新项目可以优先建议 Docker/Compose。
- 如果项目已采用 Docker，则 Codex遵守项目 Docker 规范。
- 如果项目无需 Docker，AIOS 不应因为没有 OCI 就阻塞正常开发。
- Docker/Podman 不再是 G3/G4 默认证据。
- 删除镜像 SBOM/scan/cache 的默认链路。

---

# 25. Test Suite 必须跟随功能收敛，不要为“测试数量”而保留

当前约：

```text
56 个 test 文件
13,389 行 test
```

测试多不是原罪。

但**已经删除的重型能力，其测试必须一起删除**。

## 删除/大幅减少对应测试

删除与以下能力绑定的测试：

- release candidate edge cases
- G4 publication
- host operation reliability
- verification cache
- image SBOM/image scan
- full evidence bundle
- operation migration
- release closure
- OCI 供应链审计
- Artifact Catalog 强治理

## 必须保留的测试

### GitHub/Repository

- 无 GitHub remote 时禁止 IMPLEMENT
- GitHub remote 可用时允许
- `input/` 只读
- 禁止源码副本目录
- Git 污染检测
- 一次性文件清理

### Open Source Gate

- 未完成开源调研时禁止正式编码
- 合法研究结论允许进入 IMPLEMENT

### Frontend Gate

- 有 UI 需求但无原型/批准时禁止 frontend code
- 用户批准后允许

### Worktree

- 简单任务无需 worktree
- 并行子任务 worktree 隔离
- worktree 完成后清理
- 不允许子 Agent 写其他 worktree

### Memory

- 写入有价值 memory
- 搜索
- supersede/invalid
- 不记录 Secret/聊天

### Document Impact

- API 改动检查 API_SPEC
- DB 改动检查 DATABASE
- 架构改动检查 ARCHITECTURE/ADR
- 不相关改动不要要求全量文档

### Hook

- 危险 Git 操作阻止
- `input/` 写入阻止
- 正常 pip/npm/build 不应被错误阻止

## 测试运行规则

日常：

```text
targeted pytest
ruff
git diff --check
```

里程碑：

```text
full pytest
pyright
repository check
```

**禁止“full pytest 跑完后，为了证据又重复跑同一测试子集”。**

## Coverage

Coverage 可以作为信息。

默认不再使用：

```text
fail_under = 85
```

作为所有任务的硬 Gate。

真正重要的是治理规则的正/负案例完整。

---

# 26. `pyproject.toml` 应简化

当前 dev dependencies 含：

- bandit
- build
- detect-secrets
- pip-audit
- pytest-cov
- FastAPI/HTTPX pilot 等

重构后：

## Core runtime 保留

```text
mcp
pydantic
pyyaml
typer
platformdirs（若确实仍需要）
```

## Dev 保留

```text
pytest
ruff
pyright
```

Secret 检测如果 Hook 中仍需要，可以保留轻量方案。

## 可删除

如果对应功能已删除：

```text
bandit
pip-audit
build（若不再被 Runtime 自己调用）
FastAPI
HTTPX       # ERP pilot 删除后
pytest-cov  # 如果 coverage 不再是硬要求
```

---

# 27. ERP Pilot 应删除

当前：

```text
src/codex_ai_os/pilots/erp.py
src/codex_ai_os/pilots/erp_run.py
docs/ERP_PILOT_REPORT.md
docs/PILOT_ACCEPTANCE.md
```

它是历史验证资产，不应长期进入 Core。

验证完成后由 Git 历史保存。

删除。

如果未来需要示例：

放一个非常小的：

```text
examples/
```

不要让示例业务进入 Runtime package。

---

# 28. 建议的源码文件处置矩阵

## A. 保留并重点重写

| 当前文件/模块 | 动作 | 新职责 |
| --- | --- | --- |
| `application/repository.py` | **保留+简化** | GitHub 前置 + 仓库卫生 |
| `infrastructure/documents.py` | **保留+大幅简化** | 文档存在/影响检查，不做重型 metadata/traceability |
| `infrastructure/memory.py` | **保留+简化** | 项目工程记忆 |
| `application/project.py` | **重写** | 初始化最小目录/文档/config |
| `application/worktree.py` + `adapters/worktree.py` + `infrastructure/worktrees.py` | **合并** | Codex 子 Agent Worktree helper |
| `application/authorization.py` | **重写** | 只保留真正的安全/治理硬边界 |
| `application/prototype.py` | **简化** | Frontend 原型审批 Gate |
| `cli/mcp_server.py` | **重写** | ≤10 个高层工具 |
| `cli/app.py` | **重写** | 少量用户/调试 CLI |
| `adapters/git.py` | **保留** | Git/GitHub基础查询 |
| `infrastructure/config.py` | **保留+简化** | project.yaml |
| `infrastructure/database.py` | **保留+重建 schema** | 轻量状态/Memory |

## B. 删除或从 Core 完全移除

```text
application/release.py
application/g4.py
application/verification_cache.py
application/offline_audit.py
application/environment_operations.py
application/plugin_packaging.py
application/artifact_catalog.py
application/formal_checks.py        # 如有少量通用检查则重写为 checks.py
infrastructure/operations.py
infrastructure/evidence.py
infrastructure/executions.py
domain/operations.py
domain/artifacts.py
catalog/
gates/
src/codex_ai_os/pilots/
```

## C. 大幅删除/替换

```text
application/workflow.py
infrastructure/workflows.py
domain/workflow.py

application/coordination.py
infrastructure/coordination.py
domain/coordination.py

application/environment.py
adapters/docker.py
adapters/podman.py
```

不要为了保留旧设计勉强兼容。

---

# 29. 建议的新源码结构

```text
src/codex_ai_os/
├── __init__.py
├── __main__.py
│
├── core/
│   ├── project.py
│   ├── lifecycle.py
│   ├── repository.py
│   ├── documents.py
│   ├── memory.py
│   ├── worktree.py
│   └── frontend_gate.py
│
├── adapters/
│   └── git.py
│
├── infrastructure/
│   ├── config.py
│   └── database.py
│
└── cli/
    ├── app.py
    └── mcp_server.py
```

是否一定按此目录拆分可由 Codex 判断，但**职责数量不要重新膨胀**。

---

# 30. 建议的新项目目录结构

```text
project/
├── AGENTS.md
├── README.md
│
├── input/                  # 用户原始资料，只读
├── output/                 # 最终交付给用户的成品
│
├── src/
├── tests/
├── scripts/                # 仅长期可复用脚本
│
├── docs/
│   ├── REQUIREMENTS.md
│   ├── SCOPE.md
│   ├── ARCHITECTURE.md
│   ├── OPEN_SOURCE_RESEARCH.md
│   ├── CHANGELOG.md
│   └── ADR/
│
├── .codex/
│   └── agents/             # 项目需要时使用 Codex 原生子 Agent Profile
│
└── .codex-os/
    ├── project.yaml
    ├── context/
    │   └── PROJECT_CONTEXT.md
    └── state/
        └── state.db
```

条件目录：

```text
docs/API_SPEC.md
docs/DATABASE.md
docs/TEST_PLAN.md

docs/design/
├── PROTOTYPE.html
└── UI_SPEC.md

docker/
compose.yaml
```

只有真正需要才生成。

---

# 31. `AGENTS.md` 应成为真正的“宪法”

不要再复制一套 `.codex-os/rules.md`。

Codex 本身已经有仓库指令入口。

根 `AGENTS.md` 应保持短、硬、清晰。

建议只包含：

```text
1. 不改变 Codex 原生工程方式。
2. 正式编码前必须通过 repository/open-source Gate。
3. input/ 只读。
4. 禁止复制式版本管理。
5. 受影响项目文档必须同步。
6. 前端实现前必须获得原型/UI批准。
7. 复杂并行任务使用 Codex子Agent + Worktree。
8. 一次性文件任务结束删除，不进Git。
9. 有价值决策/Bug经验写 Memory。
10. 危险Git/删除操作必须保护用户数据。
```

不要让 Codex 开始一个任务前读几十页运行时协议。

---

# 32. Git 规则也要减轻

当前 `AGENTS.md` 要求：

```text
每个 completed logical change -> commit -> immediately push
```

这对 Codex 的工作节奏太死。

新规则：

- 每个“完整逻辑任务”必须有清晰 commit。
- 子 Agent handoff 前必须 commit。
- Worktree 合并前必须 commit。
- 里程碑结束必须 push。
- 不要求每个极小修改立即 push。
- GitHub 负责历史，不通过文件副本保存。
- 禁止 force push 破坏已共享历史。

---

# 33. 开发前 GitHub Gate 的精确定义

用户要求：

> 如果项目开始时没有 GitHub 仓库，不允许开始开发代码，等待用户提供以后再开始。

实现应精确为：

### 可以做

没有 GitHub 时允许：

- 读取 input
- 需求分析
- 开源调研
- 规划
- 生成/完善项目文档
- 请求用户提供/创建 GitHub 仓库

### 不可以做

没有 GitHub 时禁止：

- 创建正式 `src/` 业务实现
- 大规模修改正式代码
- 创建用于正式实现的 Worktree
- 声称开发已开始

这样既满足版本治理要求，又不会浪费等待时间。

---

# 34. 规则优先级

不要让复杂 Policy Kernel 管每个动作。

只需要明确：

```text
1. 用户当前明确要求
2. 项目 AGENTS.md / 硬治理规则
3. 已确认项目事实（SCOPE/ARCHITECTURE/ADR/API）
4. 任务上下文
5. AIOS建议
```

注意：

如果用户明确要求破坏项目事实或绕过安全规则，Codex应指出冲突并请求确认，而不是静默执行。

不要把“AIOS内部 Runtime 状态”置于用户/Codex之上。

---

# 35. 必须保留 Codex 创造力

AIOS 不得用规则告诉 Codex：

- Controller 一定怎么写
- Backend 一定采用什么模式
- 前端一定采用哪种框架
- 测试一定怎么组织
- 必须通过 AIOS Tool 才能执行
- 必须使用 Docker 才能 build
- 必须由 AIOS Planner 决定实现步骤

AIOS 可以要求：

- 重大架构改变更新 ADR
- 新依赖记录原因
- API/DB 变化同步文档

但**技术答案由 Codex决定**。

---

# 36. 轻量化复杂度预算

为了防止再次膨胀，给 Core 设置复杂度预算。

这些不是为了形式，而是作为“超过即必须说明原因”的护栏。

建议：

- MCP Tools：**≤ 10**
- Core CLI 用户命令：**≤ 8**
- 活跃核心 docs：**约 10~15**
- 默认 Verification：**≤ 5 项**
- Core Skills：**≤ 12~14**
- 默认 Gate：**3 类**
- 默认 SQLite 核心业务表：**≤ 6**
- 不允许 Runtime 自带完整 Release/CI/CD/SBOM 平台

若必须超过，需写 ADR 说明：

> 这个新增复杂度具体解决七个核心目标中的哪一个？

答不出来就不做。

---

# 37. 数据与历史清理计划

当前源码快照中 `.codex-os/` 已存在：

- `state.db`
- 多个 DB backup
- execution logs
- artifacts
- governance repair artifacts

这些不属于源代码资产。

重构执行时：

1. 检查 Memory 表是否存在有价值记录。
2. 导出有价值 Memory。
3. 清空旧 Runtime state/logs/artifacts。
4. 重建轻量 DB。
5. 导入 Memory。
6. `.codex-os/state|logs|artifacts|context` 均保持 Git ignore。
7. 不在仓库建立 backup 历史目录。

同样清理：

```text
dist/
.pytest_cache/
.ruff_cache/
__pycache__/
.coverage
已完成 .worktrees/
```

`.venv/` 仅本地环境，不进入 Git。

---

# 38. 重构实施方式

## 38.1 Git 方式

必须：

```text
从当前干净分支
  ↓
创建单独 refactor 分支
  ↓
直接修改/删除现有代码
  ↓
不要复制源码
```

建议分支：

```text
refactor/governance-core
```

禁止：

```text
src_v2/
AIOS-new/
AIOS-lite-copy/
legacy/
backup/
```

Git 历史就是旧代码备份。

## 38.2 可以推翻重写的条件

如果某模块：

- 80%以上代码服务于已经取消的能力；
- 为兼容旧 G4/Release/Evidence 导致实现明显复杂；
- 很难安全抽取核心功能；

直接删除重写比补丁式修改更优。

本次重构允许 breaking change。

**不要为过去错误架构维持长期兼容。**

---

# 39. 建议实施阶段

## Phase 1 — Freeze & Delete

先不加功能。

完成：

- 固定最终产品定位
- 新 ADR：Governance Core
- 删除 Release/G4/Verification Cache/Host Operation/Evidence 重系统
- 删除 ERP Pilot
- 删除历史 Proposal/Release closure 文档
- 清理 Runtime 生成物

验收：

- 项目能 import
- 现有保留模块依赖关系清楚
- 没有复制式旧代码目录

## Phase 2 — Rebuild Governance Core

重写：

- Project Init
- Repository Guard
- Document Guard
- Lifecycle
- Hook
- MCP/CLI

实现三 Gate：

- Code Start
- Frontend Approval
- Finish

## Phase 3 — Restore Memory + Worktree

实现：

- 轻量 Memory
- Codex native subagent worktree helper
- handoff/cleanup
- 不实现自有 Agent Scheduler

## Phase 4 — Slim Skills & Docs

- 删除 implementation/execution/release skills
- 合并 UI/interaction 设计资产
- 文档收敛
- 新项目模板收敛
- 恢复 `input/` / `output/`

## Phase 5 — Rewrite Tests

围绕最终目标重写测试。

删除所有已删除模块测试。

禁止为历史 Runtime 兼容增加大量测试。

## Phase 6 — Dogfood

用 AI Engineering OS 自己开发一个真实小项目验证：

### Case 1

无 GitHub：

- 可以分析
- 不能写正式代码

### Case 2

普通后端需求：

- Open Source Research
- Codex自主开发
- 文档影响更新
- 清垃圾

### Case 3

Frontend：

- Prototype/UI
- 用户 Gate
- 才允许实现

### Case 4

复杂需求：

- Codex原生子 Agent
- Worktree隔离
- Review/Merge/Cleanup

验证通过才算完成。

---

# 40. 必须通过的最终验收

## A. 不限制 Codex 原生能力

以下必须能够正常执行，不被 AIOS无理由阻止：

```text
pip install
uv sync
npm install
npm build
正常 shell
正常测试
正常局部 Git
Codex Plan Mode
Codex文件编辑
Codex原生子Agent
```

只在真实治理/安全边界发生冲突时拦截。

## B. GitHub

新项目无 GitHub：

```text
formal code write = BLOCKED
```

提供 GitHub 后：

```text
formal code write = ALLOWED
```

## C. 开源研究

新需求没有研究结论：

```text
IMPLEMENT = BLOCKED
```

完成研究并记录决策：

```text
IMPLEMENT = ALLOWED
```

## D. 仓库卫生

尝试创建：

```text
src_v2/
project_backup/
xxx_final.py
fix_x_v2.py
```

必须阻止或在 Finish Gate 失败。

## E. `input/`

Codex：

```text
read input = ALLOWED
write/delete/rename input = DENIED
```

## F. `output/`

只保存最终用户交付物。

禁止：

- 临时测试
- cache
- source backup
- debug logs

## G. Frontend

存在 frontend/UI impact：

```text
no prototype/UI approval -> frontend implementation BLOCKED
```

用户批准：

```text
ALLOWED
```

## H. Worktree

复杂并行任务：

- Codex能用自己子Agent
- 子Agent进入独立Worktree
- 不写其他Worktree
- 完成后Review/Merge
- Worktree及时清理

## I. Docs

只更新受影响文档。

不能因为修改一行后端代码强制重写全部 10+ 文档。

## J. Temp Cleanup

任务结束：

```text
tmp/debug/cache/一次性脚本 = 删除
git status = 无垃圾
```

## K. Memory

有价值经验自动留下。

无价值运行记录不进入 Memory。

## L. 测试

不再出现：

```text
full pytest
→ 再重复 plugin pytest
→ 再重复 agent pytest
→ 再重复 MCP pytest
```

---

# 41. 明确的“不要再做”清单

Codex 在本轮重构中不得重新添加以下复杂度：

- 自有 LLM/Agent Loop
- 自有 Coding Agent
- 自有 Tool Runtime 替代 Codex
- 自有复杂 DAG Scheduler
- 默认强制 OCI Execution
- 默认 SBOM
- 默认镜像扫描
- 默认 dependency audit
- Verification Cache
- Release Candidate Runtime
- GitHub Release Publisher
- Host Operation Lease/Reconciliation
- 每条命令的审计 Evidence
- Artifact Catalog 大系统
- 61份文档式事实体系
- 每个阶段都需要 hash bundle
- 为旧错误架构做长期兼容

如果未来用户真的需要其中某项：

单独作为可选插件/Profile讨论。

---

# 42. 开源复用最终决定

本次重构遵循“先复用、后自研”，但不为了复用引入新的重型框架。

直接依赖/复用：

- **Git / GitHub**：版本事实和历史
- **Git Worktree**：Codex 子 Agent 隔离
- **SQLite**：轻量项目状态和 Memory 索引
- **Codex Plugin/Hook/Skill/Agent 原生机制**：治理注入
- **MCP SDK**：仅保留少量治理工具接口

只提取思想，不引入 Runtime：

- Aider：Git/diff 工作纪律
- OpenHands：Workspace 隔离思想
- Claude/Codex 项目规则生态：项目上下文/规则注入思想

不引入：

- LangGraph
- CrewAI
- MetaGPT
- AutoGen
- OpenHands Runtime

原因：

这次目标不是重新造 Agent。

---

# 43. 完成后的理想体验

用户只需要说：

> “帮我做一个 XX 项目 / 加一个 XX 功能。”

Codex 自动：

```text
1. 读 AGENTS.md / 项目上下文
2. 检查 GitHub
3. 理清需求和范围
4. 先找开源项目
5. 决定直接用 / 二开 / 提取 / 自研
6. 如涉及前端，先给用户看原型/UI
7. Codex 自己决定专业实现方式
8. 复杂时自行使用子 Agent + Worktree
9. 自己开发、调试、测试
10. 更新受影响项目文档
11. 清理所有一次性文件
12. 沉淀真正有价值的项目经验
13. 用 Git 记录版本
```

用户不需要：

- 手工调用一堆 MCP Tool
- 理解 G0-G4
- 理解 Evidence Bundle
- 管理 SBOM
- 管理 Verification Cache
- 管理 Host Operation
- 管理 Runtime lease
- 管理几十份规格文档

这才是 AI Engineering OS 的成功状态。

---

# 44. 给 Codex 的最终执行指令

> **请把本文件视为本轮重构的最高级产品与架构目标。**
>
> 先完整审查当前仓库，再制定重构 Plan；不要边读边改。
>
> 本轮允许大规模删除、合并、重写现有模块。不要因为“代码已经写了很多”就保留没有用户价值的复杂系统。
>
> 关键判断标准只有一个：
>
> **某段代码是否直接帮助 AI Engineering OS 完成以下目标：GitHub、开源优先、仓库卫生、项目文档、Codex原生子Agent+Worktree、前端原型/UI Gate、任务后清垃圾，以及轻量长期工程记忆。**
>
> 如果答案是否定的，而且没有独立必要性，就删除。
>
> 不建立第二套源码目录，不复制旧实现。使用 Git 保存历史。
>
> Codex 的专业工程能力是资产，不是需要被 AIOS 替代或微观管理的对象。
