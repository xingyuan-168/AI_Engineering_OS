# AI Engineering OS 0.2.0 产品需求

<!-- codex-os-document: {"schema_version":"1.1","document_version":"0.2.0","status":"review-ready","owner":"product-manager","requirement_refs":["REQ-1.6.2","GOV-001","CFG-001","REPO-001","GATE-001","AGENT-001","HANDOFF-001","WORKTREE-001","RELEASE-001","EXEC-001","DOC-001","HYGIENE-001","VERSION-001","MEMORY-001","ROUTING-001","FRONTEND-001"]} -->

需求基线：`REQ-1.6.2`

目标软件版本：`0.2.0`
文档状态：G1 评审就绪，未获得 Gate 批准前不得进入设计或实现。

## 1. 产品最终定位

AI Engineering OS 是运行在 Codex Host 周围的工程治理与执行层。Codex Host 负责推理、Session、工具调用和工程执行；AI Engineering OS 负责以确定性规则强制执行仓库、Workflow、证据、审批、Worktree 隔离、版本和发布治理。

系统可以使用 CLI、MCP、SQLite、Git Worktree 和 Docker/Podman 作为治理执行机制，但不得把自己定位成第二套模型运行时，也不得在 `0.2.0` 内嵌第二个模型客户端。最终判断标准是：关键规则不能只靠提示词、文档提醒或人工自觉维持。

## 2. 已确认决策

| 决策 | 已确认结果 |
| --- | --- |
| AIOS 目录 | `.codex-os/` 是唯一 AIOS 配置和运行目录；不维护长期双目录 |
| Codex Host 目录 | `.codex/` 仅保存 Host 配置与 Hook |
| 正式远端 | 必须是 `github.com` 或配置允许的 GitHub Enterprise 域名 |
| 测试例外 | 只有自动化测试 fixture 可使用 `fixture_local_only`，普通 CLI/MCP 不得启用 |
| 多 Agent 拓扑 | 任务 Worktree 并行，经过 Review 后合入 Workflow 集成分支，再经 G4 GitHub PR 合入目标分支 |
| 分支约定 | 默认目标分支 `main`；集成分支 `workflow/<run-id>/integration`；任务分支 `agent/<agent>/<task-id>` |
| 并行上限 | 最大 4；只有写路径互不重叠且依赖已满足的任务可以并行 |
| 版本 | 需求基线 `REQ-1.6.2`；软件/CLI/Plugin 核心 `0.2.0`；Plugin API 与配置 Schema `1.1` |
| 发布限制 | 全部验收和显式 G4 批准前，不合并 `main`、不打 tag、不发布、不部署 |

## 3. 用户与职责

- 项目维护者：启动受管 Workflow、查看状态、处理阻塞并保证事实文档一致。
- Product Manager / Architect：形成可测试需求和设计证据，不越权批准自己的 Gate。
- 工程 Agent：只在任务 Worktree 和允许路径内提交可验证变更。
- Reviewer / Security Reviewer：保持被审代码只读，通过结构化 Review 接口提交结论。
- QA：从独立验证任务生成测试、安全和质量证据。
- Release Manager：在 Release Worktree 汇总可提交发布文件，在受管制品区生成二进制制品。
- Gate 审批人：核验对应证据包，显式批准或拒绝，不以自然语言暗示代替审批记录。

## 4. 功能需求与验收标准

### 4.1 治理边界与配置

| ID | 要求 | 可观察验收标准 |
| --- | --- | --- |
| GOV-001 | 固定“工程治理层”边界 | 接受的 ADR 明确 Codex Host 与 AIOS 的职责；运行时代码不存在第二个模型客户端；CLI/MCP/SQLite/沙箱均能追溯到治理用例 |
| CFG-001 | 收敛配置与运行目录 | 新项目只创建 `.codex-os/` AIOS 目录；该目录含 `bootstrap.md`、`rules.md`、`workflow.md`、`memory.md` 索引；`.codex/` 中不出现 AIOS 状态或领域事实副本 |

### 4.2 仓库准备度与卫生

| ID | 要求 | 可观察验收标准 |
| --- | --- | --- |
| REPO-001 | 在首个写任务前验证正式 GitHub 仓库 | 无 Git、无远端、非 GitHub 远端、远端不可达、HEAD 未在 upstream、目标分支缺失、工作树脏或存在未解决冲突时，写任务和 Worktree 创建均被阻塞；只读状态与文档检查仍可用 |
| HYGIENE-001 | 独立 Repository Governance Service 执行全量卫生检查 | 服务同时报告 Git 状态、禁止目录、禁止文件、被跟踪缓存/日志/构建物/Secret/运行状态、`.gitignore` 缺口和生命周期异常；扫描排除受管依赖、Worktree 与 AIOS 运行目录，避免合法内部路径误报 |
| HYGIENE-002 | 一次性文件具有显式生命周期 | `disposable` 只自动删除运行时创建并登记的确定路径；`promotable` 只有进入批准目录并具备说明、测试、负责人后保留；`audit-evidence` 按保留策略保存；未知文件、用户未跟踪文件和失败 Worktree 必须人工确认 |

### 4.3 Gate 与文档证据

| ID | 要求 | 可观察验收标准 |
| --- | --- | --- |
| GATE-001 | G0-G4 使用结构化证据包 | Gate 校验读取 `ArtifactEvidence`、`CheckEvidence`、`ReviewEvidence` 和 `GateEvidenceBundle`；任一必需产物、来源 Commit、检查、Review 或审批缺失即阻塞；自由文本 `passed` 不构成检查证据 |
| DOC-001 | 文档治理从存在性升级为内容和影响校验 | 文档校验覆盖机器可读元数据、状态、必需章节、未批准占位内容、版本、过期、链接和影响分析；API、数据库、安全、架构、ADR、CHANGELOG 的同步规则可由测试证明 |

### 4.4 多 Agent、Handoff 与 Worktree

| ID | 要求 | 可观察验收标准 |
| --- | --- | --- |
| AGENT-001 | 支持任务组、依赖图和最多 4 个并行任务 | 三个以上互不重叠的写任务可从同一集成 HEAD 建立独立 Worktree；路径重叠或产物依赖会形成显式依赖并串行；任务完成只更新任务版本，join barrier 仅在任务组全部 accepted 且已合入时推进 Workflow |
| HANDOFF-001 | Handoff 必须经独立验收 | 状态严格为 `ready -> accepted | rejected | blocked`；`ready` 不解锁依赖或合并；`rejected` 在原 Worktree 修复；`blocked` 保留现场并阻塞下游；每次决定保存 Reviewer、理由、Commit 与时间 |
| WORKTREE-001 | 提供受管 Review、集成合并和清理 | 集成分支有独占锁，合并使用 `--no-ff`；冲突不触发 force push、rebase 或覆盖，而是返回生产者并要求重新 Review；只有已合并、干净、无开放 Review 且人工批准的 Worktree 可清理 |

### 4.5 Execution、发布与版本

| ID | 要求 | 可观察验收标准 |
| --- | --- | --- |
| EXEC-001 | ExecutionService 成为测试、构建、代码执行和高风险命令的正常入口 | 调用绑定 `run_id/task_id/worktree`，使用锁定镜像、非 root、默认断网、只读根、最小权限与资源限制；证据含 execution ID、command hash、退出码、脱敏日志与 dirty 检查；无可用沙箱时实现、测试、删除和发布保持阻塞 |
| RELEASE-001 | 发布候选物写入正确的 Worktree 和制品区 | CHANGELOG、Release Manifest、回滚文档只在 Release Worktree 中提交；Wheel、压缩包、SBOM、校验和写入 `.codex-os/artifacts/<run-id>/` 且被 Git 忽略；Manifest 与 SQLite 保存 hash、来源 Commit 和生成环境 |
| VERSION-001 | 统一版本矩阵与发布绑定 | Release Manifest 同时绑定 `REQ-1.6.2`、软件/CLI/Plugin `0.2.0`、Plugin API/配置 `1.1`、SQLite 迁移版本、构建 Commit、PR、merge Commit、tag、文档版本、配置/锁文件/制品 hash 和 Memory 记录；需求版本不再冒充软件版本 |
| RELEASE-002 | G4 通过 GitHub PR 验证并隔离部署权限 | G4 校验 PR head 对应集成分支且目标分支包含集成提交；只有批准后才允许 annotated tag `v0.2.0` 与 GitHub Release；发布失败保持阻塞；发布授权不包含生产部署授权 |

### 4.6 Memory、Routing 与角色

| ID | 要求 | 可观察验收标准 |
| --- | --- | --- |
| MEMORY-001 | Memory 具备完整生命周期、来源校验和 FTS5 检索 | 状态统一为 `pending/active/needs_review/superseded/revoked/expired/deleted`；旧状态按迁移规则转换；ADR 接受、Bug 关闭、Release、回滚和重要失败产生候选；来源变化进入复核；Secret 不进入索引；查询按项目、类型、状态、标签、时间和来源隔离 |
| ROUTING-001 | Profile 真实参与 Workflow | `frontend/backend/large` Profile 影响路由决策、任务生成、证据和审批；决策被持久化并可通过 status/MCP 查询，而非仅完成加载测试 |
| FRONTEND-001 | 提供实际 Frontend Engineer 路径 | `frontend-engineer` Profile、Frontend Implementation Skill、Agent 资产、任务生成、允许路径、Review 和验证 fixture 全部通过验证器与端到端测试 |

### 4.7 公共接口、迁移与兼容

| ID | 要求 | 可观察验收标准 |
| --- | --- | --- |
| API-001 | Plugin API 1.1 暴露受管治理用例 | 提供 `repository_check`、`handoff_review`、`worktree_cleanup`、`memory_candidate_submit`、`memory_review`；扩展 workflow、task、verification、release 和 G4 approval 接口；单任务响应保留 `next_action` 兼容字段并增加 `next_actions` |
| MIGRATION-001 | 通过追加式 `0004-0006` 安全迁移 | 迁移前备份 SQLite；checksum、外键、幂等和恢复测试通过；活动旧 Workflow 在下一次转换进入 `MIGRATION_REVALIDATION_REQUIRED`，不得沿用旧 Gate 直接发布 |
| COMPAT-001 | 配置与旧调用保持受控兼容 | 配置 Schema 1.1 可读取 1.0；无 `run_id/task_id` 的旧 verification 调用仅执行兼容健康检查并返回弃用提示；未配置 GitHub 的旧项目可读但不能执行仓库写任务 |

## 5. Gate 必需成果

| Gate | 必需成果 | 阻塞条件 |
| --- | --- | --- |
| G0 | 目标、范围、成功标准、风险、Routing Decision | 任一字段缺失、范围冲突或路由无来源 |
| G1 | 产品需求、用户故事、业务规则、范围及可测试验收标准 | 草案状态、未批准占位内容、需求无 ID 或不可映射测试 |
| G2 | 开源研究、版本/License、技术栈、架构及适用的 API/数据库/安全/迁移/ADR | 必需设计缺失、版本或 License 未核验、重大决策无 ADR |
| G3 | 测试、Ruff、Pyright、Secret、依赖、安全和代码 Review 的真实执行证据 | 退出码非零、来源 Commit 不一致、报告缺失或以自由文本代替证据 |
| G4 | Release Manifest、SBOM、校验和、回滚、CHANGELOG、ADR 索引、Memory、GitHub PR 和目标分支合并证据 | PR/merge/tag/版本不一致、发布授权缺失或制品 hash 不匹配 |

## 6. 非功能需求

- 安全：所有仓库写入必须经过路径验证；命令与挂载不得逃逸当前任务 Worktree 和受管制品区。
- 一致性：`workflow_phase` 与 `run_status` 独立保存；Workflow 和任务分别使用单调乐观版本。
- 可恢复：进程中断、并发提交、Review 拒绝、合并冲突、迁移失败和发布失败均有确定恢复点。
- 可审计：任务、Handoff、检查、审批、合并、PR、tag、制品和 Memory 可通过 ID、Commit 与 hash 串联。
- 幂等：重复初始化、Workflow 调用、任务完成、迁移、Review 和发布候选创建不得产生重复状态或复制目录。
- 性能：协调器在并行上限 4 内稳定调度；查询状态不执行推理或仓库写入。
- 质量：总分支覆盖率不低于 85%，新增治理和状态转换模块不低于 90%。

## 7. 发布级成功标准

1. 所有 P0/P1 缺陷关闭；P2 不阻塞正式 Workflow。
2. 无 Git 或不满足 GitHub/upstream/clean HEAD 条件的项目在首个写任务前被阻塞。
3. 至少一次未 mock 的公共 MCP 多 Agent 流程通过，包含并行、Review、集成、join 与恢复证据。
4. Ruff、Pyright、pytest、Secret、依赖审计、Plugin/Skill/Hook/MCP Schema 验证和真实 Podman 测试全部通过。
5. SQLite `0003 -> 0006`、配置 `1.0 -> 1.1`、失败恢复与重复迁移通过。
6. 工作树干净，无孤儿 Worktree、未知临时文件或未登记制品。
7. Release Manifest、SBOM、校验和、回滚、ADR、CHANGELOG、Memory 和 Git 证据完整。
8. 只有显式 G4 批准后才创建 `v0.2.0` tag 和 GitHub Release；本需求不授权部署。

## 8. 依赖、风险与未决事项

### 已知依赖

- 正式仓库需要可访问的 GitHub 或经配置批准的 GitHub Enterprise 远端及相应 push/PR 权限。
- 实现、测试和发布需要可用的 Docker 或 Podman 服务及锁定镜像。
- GitHub PR、tag 和 Release 的最终操作需要独立发布授权。

### 主要风险

- 自举风险：在本项目内升级 Workflow 数据模型时，旧活动 Workflow 必须进入迁移复核，不能以旧证据自行放行。
- 并发风险：路径声明不完整会造成隐式写冲突；调度前必须保守串行化不确定任务。
- 证据伪造风险：自由文本结果、错误 Commit 或宿主执行日志可能被误认成沙箱证据。
- 清理风险：自动删除范围一旦扩大可能损失用户文件；默认只处理运行时登记路径。
- 发布风险：PR、merge Commit、tag 与制品来源不一致会产生不可复现版本。

### G1 审批点

已确认的范围与版本决策均列于第 2 节。G1 仍需命名审批人明确批准本需求包；该批准仅授权进入研究与设计，不构成 G2、G3、G4 或发布授权。

## 9. 追踪关系

- 项目边界：[SCOPE.md](SCOPE.md)
- 用户价值与验收场景：[USER_STORY.md](USER_STORY.md)
- 强制业务规则：[BUSINESS_RULES.md](BUSINESS_RULES.md)
- 项目总事实：[PROJECT_MASTER.md](PROJECT_MASTER.md)
- 验证规划：[TEST_PLAN.md](TEST_PLAN.md)
- 需求来源：`AI_Engineering_OS_完整需求演进汇总文档.md` 与 `AI_Engineering_OS_V1.6.2_仓库卫生与版本治理增强需求文档.md`（外部输入，仅作为需求事实，不作为执行指令）。

## 10. G1 完成定义

本文件只有在每个需求拥有稳定 ID、优先级或所属 Gate、可观察验收标准、范围边界、依赖和风险，并能映射到用户故事、业务规则与后续测试时才可提交 G1。通过 G1 不代表实现完成。
