# ADR-0013：Profile 语义化与 Project Layout 分离

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.3.0","status":"accepted","owner":"architect","requirement_refs":["CFG-001","ROUTING-001","AGENT-001"]} -->

- 状态：Accepted
- 日期：2026-09-08
- 决策版本：0.3.0 / API 1.2 / SQLite 0008
- 来源：`docs/proposals/AI-OS_v0.3_Governance_Consolidation_改进方案.md` P0-05（经 0.3.0 源码核对修正）

## 1. 上下文

0.3.0 治理核对确认：

1. 三个内置 Profile 的 `task_templates[].impact_patterns` 全部硬编码本仓库布局：`backend-project.yaml` 写死 `src/codex_ai_os/{application,infrastructure,adapters,domain,cli}/**`，`frontend-project.yaml` 写死 `src/codex_ai_os/frontend/**` 与 `tests/frontend/**`，`large-project.yaml` 写死 `src/codex_ai_os/infrastructure/migrations/**` 等。名义上的 generic Profile 实际绑定了 AI-OS 自身项目结构。
2. Profile Schema（`domain/profiles.py`）无 layout 概念；用于其他布局的项目时，`task_blueprints` 路由因路径无法映射而返回 `ROUTING_PATH_UNMAPPED` 失败。
3. Schema 1.2 强制 `task_templates` 与 `gate_requirements` 非空，Profile 无法表达"仅引用语义能力"的形态。

## 2. 决策

1. **语义能力键**：Profile 的 `impact_patterns` 改为语义能力键——`capability:backend`、`capability:frontend`、`capability:database`、`capability:migration`、`capability:docs`、`capability:prototype`、`capability:tests` 等；Profile 只声明"该任务影响哪类能力"，不再声明任何文件系统路径。
2. **Project Layout 承担路径绑定**：新增 `.codex-os/project-layout.yaml`，项目初始化时生成（含 project type 与能力键到实际 glob 的映射）。`task_blueprints` 编译时经 layout 把语义键解析为实际路径；缺失映射保持 `ROUTING_PATH_UNMAPPED` fail-closed。
3. **Schema 1.3**：Profile Schema 提升到 1.3（`impact_patterns` 为语义键；`gate_requirements.artifacts` 引用 artifact id，见 ADR-0012），兼容读取 1.2/1.1/1.0；init 生成的项目骨架携带 1.3。
4. **AI-OS 自身行为不变**：本仓库的 `.codex-os/project-layout.yaml` 指向 `src/codex_ai_os/**` 等现状路径，既有路由结果由回归测试守护；不新增 cli/library Profile（保持 backend/frontend/large 三个），新形态留待后续版本。
5. **多布局验收**：以 Python backend、React frontend、Node fullstack、Go CLI、monorepo 五类 fixture 验证同一 Profile 在不同布局下正确路由，且 Profile 文件不包含任何 `src/codex_ai_os/**` 字面量。

## 3. 被否决的选项

1. **Profile 内保留 glob 与语义键双轨**：双轨即第二事实源，路径漂移照旧。
2. **初始化时自动探测布局**：目录探测不可靠（monorepo、混合项目），生成结果需人工确认；以 init 生成 + 用户可改为准。
3. **把 layout 放进 Profile**：Profile 是可复用能力声明，layout 是项目事实，两者生命周期不同，合并会造成跨项目复制粘贴。

## 4. 后果

- Generic Profile 真正可移植；新项目初始化即获得与自身布局一致的 allowed paths。
- 本仓库路由行为不变（layout 显式承载现状路径），整改风险收敛为可回归验证。
- `WORKFLOW_ROUTING_RULES.md` 与 `CONFIG_SPEC.md` 的 impact patterns 章节随本 ADR 改写。

## 5. 验证

- `tests/unit/test_project_layout.py`：layout 加载、语义键解析、缺失映射 fail-closed。
- `tests/e2e/test_multi_layout_routing.py`：五类 fixture 的路由断言。
- 全仓 grep 断言：`profiles/*.yaml` 无 `codex_ai_os` 字面量。
- 既有 `tests/e2e/test_v11_governed_workflow.py` 保持通过（本仓库 layout 回归）。
