# ADR-0016：Governance Core 轻量化大重构

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.3.0","status":"accepted","owner":"architect","requirement_refs":["REPO-001"]} -->

- 状态：Accepted
- 日期：2026-09-10
- 决策版本：重构起点 0.3.0（版本号待 Dogfood 通过后由用户一次性定稿）
- 来源：`input/AI_Engineering_OS_Governance_Core_轻量化大重构实施规格.md`（用户提供的重构输入，只读）+ 两轮人工评审反馈（共 18 条，已全部并入）

## 1. 上下文

对当前仓库的逐项审计确认了规格文档的判断：系统已明显过度工程化。`session_start.py` 自称 execution authority；`pre_tool_use.py` 全局拦截 pip/npm/pnpm/yarn/cargo/sed -i 等正常工程命令；`repository.py` 把 `input/` 列入禁止目录；`domain/workflow.py` 维护 G0-G4 + 10 阶段状态机（application/workflow.py 2467 行、infrastructure/workflows.py 1339 行）；`verification.py` 默认 17 项检查且 full pytest 后重复跑 5 个 pytest 子集作"证据"；MCP 恰好 30 个工具、CLI 12 个命令；release/g4/verification_cache/evidence/operations/environment_operations 等重系统合计逾万行；docs 61 份、Skills 21 个、SQLite 迁移 0001~0008；`.codex-os/` 堆积 logs/artifacts/backups。

AI Engineering OS 的最终定位不是 Agent 平台、Coding Runtime、CI/CD 或供应链审计系统，而是 **Codex 的工程治理层**：约束"能否做、何时做、做完留下什么"，不约束 Codex 怎么做专业工作。

## 2. 决策

1. **定位**：只解决七件事 + 轻量记忆——GitHub 前置、开源调研优先、禁止复制式版本管理、受影响文档同步、Codex 原生子 Agent + Worktree 隔离、前端原型/UI 人工确认、任务结束清垃圾，以及项目长期工程记忆。
2. **无状态 Gate Evaluator**：废除 G0-G4 状态机与 PREPARE→…→DONE 生命周期迁移。保留三个 Gate，只回答"当前允许/不允许以及为什么"，不决定 Codex 下一步具体干什么：
   - **Code Start**：GitHub remote 存在可达；"脏乱"精确判定（仅复制式版本目录/文件、Git 污染、未解决冲突才阻塞；用户已有未提交工作与当前任务自身修改不算脏乱）；开源调研**分层适用**——新项目/新模块/重大功能/新技术栈/新第三方集成/明显有成熟轮子必查，typo/小 Bug/补测试/既有方案内小修改豁免。
   - **Frontend Approval**：仅前端实质变更启用——新页面/新交互流程/重大 UI 重构须原型 + UI 方案 + 用户批准；改文案/修 CSS 错位/修已有组件 Bug 豁免。
   - **Finish**：任务相关测试通过、受影响文档同步、仓库卫生、一次性文件清理、Memory 已写入或明确不需要、无目录副本版本。
3. **删除清单**：release、g4、verification_cache、offline_audit、environment_operations、environment、plugin_packaging、artifact_catalog、coordination（三层）、execution、routing、maintenance、responses、public_contracts、formal_checks、operations、evidence、executions、workflows、events、domain 的 operations/artifacts/invocation/profiles/environment/workflow、adapters 的 docker/podman、pilots 整目录、catalog/、gates/、profiles/、自用 docker/ 与 compose.yaml；G0-G4 五层 Gate 与 Artifact Catalog 一并删除。
4. **接口收敛**：MCP 30 → 7（project_init、governance_check(stage=start|frontend|finish)、approval_record、context_refresh、worktree_manage、memory_search、memory_record）；CLI 12 → 7（init、check、memory、worktree、finish、mcp、doctor）；默认验证 17 → 5（targeted tests、ruff、git diff --check、repository hygiene、必要时 pyright）。
5. **Memory 双层存储 + 单写者**：Git 跟踪的 `docs/memory/memory.jsonl` 为唯一事实源（type/title/summary/source/source_commit/tags/status；4 类型 decision/bug/lesson/pattern，3 状态 active/superseded/invalid）；SQLite 仅做本机可重建搜索索引（`codex-os memory reindex`）。子 Agent 不直接写长期 Memory，只提交 candidate；**主 Codex 在任务 Finish 时统一写入**，避免多 Worktree 并行 append 冲突。
6. **Hook 重写**：SessionStart 只做三件事（告知启用、提醒读 AGENTS.md 与事实文档、提醒治理前置）；PreToolUse 语义为"**保护用户资产**"而非"禁止专家工具"——force push/删远端分支/volume 破坏无条件拦截；`git reset --hard`、`git clean -f`、递归删除按路径上下文判定（主工作区/用户文件/input/ 拦截，Codex 自建并登记的 disposable Worktree 与系统临时目录放行）；pip/npm/pnpm/yarn/poetry/cargo/sed -i 全面放行。
7. **目录契约**：`input/` 为受保护只读用户输入（不再是禁止目录）；`output/` 仅存最终交付物并做纯净性检查；`PROJECT_CONTEXT.md` 为 Git-ignore 的派生缓存，可随时重建，永不作为事实源。
8. **Docker 降级**：删除 AIOS 自有 Docker/Podman 执行运行时；project_init 提供可选的项目开发环境模板（Dockerfile + compose.yaml）。Docker 服务 Codex，AIOS 不强迫任何命令经过自己的执行器。
9. **Out of Scope（不做占位、不做空接口）**：strict assurance Profile、SBOM、镜像扫描、dependency audit、Verification Cache、Release Candidate/GitHub Release 发布器、Host Operation lease/对账、每命令 Evidence、自有 DAG 调度、自有 Agent/Tool Runtime。未来确有需求以独立插件讨论。
10. **Secret 检测不自研**：保留 detect-secrets 轻依赖，只扫 staged diff/本次修改，`core/checks.py` 薄封装。
11. **复杂度预算**（MCP ≤8、CLI ≤8、验证 ≤5、Skills ≤9、Gate 3 类、SQLite ≤6 表、活跃 docs 10~15）**仅为 ADR 设计护栏**，供人工 review 对照，不实现任何运行时计数/检测代码。
12. **Agent 边界**：AIOS 不创建、调度或管理 Agent；Codex 主会话负责规划和执行，必要时由 Codex 自己调用原生子 Agent。Developer/Tester/Reviewer 如存在，只是 Codex 原生子 Agent Profile。
13. **Git 方式**：被否决的 artifact-catalog WIP 保存于 `archive/pre-governance-core` 分支（不进入未来主线 lineage）；重构在 `refactor/governance-core` 分支进行，每 Phase 一个 Conventional Commit 并推送；全程不创建任何复制式目录，旧实现仅存 Git 历史。版本号在 Phase 6 Dogfood 全部通过、用户确认后一次性定稿（package + CHANGELOG + tag）。

## 3. 被否决的选项

1. **保留重系统做向后兼容**：违反"Git 保存历史，工作树只保存当前正确实现"；本次接受 breaking change。
2. **strict assurance 占位接口**：是未来重新长回 SBOM/Trivy/Evidence Runtime 的入口，连空接口都不做。
3. **轻量生命周期状态机（PREPARE→…→DONE）**：仍是状态机思维，会产生隐藏状态迁移；无状态评估器同输入同输出。
4. **Memory Runtime 化/多表设计**：单写者规则 + 单 JSONL 足够，不需要并发控制机器。
5. **开源调研/前端 Gate 一刀切**：会让"先查开源""先画原型"成为新的形式主义，必须分层适用。
6. **运行时复杂度预算检测**：防膨胀的膨胀检测系统，本身即膨胀。
7. **把 WIP 提交进主线 lineage**：改用 archive 分支隔离废弃设计。

## 4. 后果

- 本轮为 breaking change：旧 G0-G4、Evidence、Release、Host Operation 语义不再兼容；旧迁移链（0001~0008）废弃，轻量 schema 单迁移重建（当前运行库已确认为空，无数据迁移需求）。
- 文档从 61 份收敛至约 13 份；被删文档不建 archive 目录，Git 历史即归档。
- AGENTS.md 重写为 10 条宪法；Git 节奏放宽为"完整逻辑任务 commit、handoff/合并前 commit、里程碑 push"。
- `.codex-os/` 收敛为运行状态目录（project.yaml、state.db、派生 context），全部 Git ignore。
- 验收标准：规格 §40 A-L 全部通过 + Dogfood 四案例 + 复杂度预算人工核对。

## 5. 验证

- Phase 6 Dogfood：① 无 GitHub 阻断/放行；② 普通后端需求全流程；③ 前端 Gate 与豁免路径；④ 子 Agent + Worktree 隔离与清理。
- 测试正负案例覆盖：Gate A 脏乱精确判定（用户未提交工作放行）、开源调研分层（豁免类误拦/必查类漏拦均失败）、前端 Gate 分层、`input/` 只读、副本目录拦截不误伤 `api/v1/`、危险命令主工作区拦截/自有 Worktree 放行、Memory reindex 与单写者、pip/npm/build 不被错误阻止。
