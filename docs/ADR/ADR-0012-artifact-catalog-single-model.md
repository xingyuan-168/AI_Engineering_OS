# ADR-0012：Artifact Catalog 与前端设计单一模型

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.3.0","status":"accepted","owner":"architect","requirement_refs":["GATE-001","DOC-001","FRONTEND-001"]} -->

- 状态：Accepted
- 日期：2026-09-08
- 决策版本：0.3.0 / API 1.2 / SQLite 0008
- 来源：`docs/proposals/AI-OS_v0.3_Governance_Consolidation_改进方案.md` P0-03/P0-04（经 0.3.0 源码核对修正）

## 1. 上下文

0.3.0 治理核对确认：

1. 同一批 Artifact 路径在约六处独立维护：`domain/workflow.py` 的 `PHASE_DEFINITIONS`（阶段 allowed_paths 与输入工件）、`application/workflow.py` 的 `_input_artifacts_for`、`profiles/frontend-project.yaml`（required_artifacts 与 gate_requirements.artifacts）、`gates/G0-G4.yaml`（documents 字段）、`templates/project_docs.py`（初始化模板键）与 `tests/e2e/test_v11_governed_workflow.py` fixture。全仓不存在任何 catalog 实现。
2. 典型 Contract Drift：初始化模板只生成 `docs/design/UX_RESEARCH.md`、`docs/design/USER_FLOW.md`、`docs/design/WIREFRAME.md`、`docs/design/UI_SPEC.md`，而 Workflow 阶段、Profile 与 Gate 等待的是 `docs/PRODUCT_DESIGN.md`、`docs/INTERACTION_DESIGN.md`、`docs/UI_DESIGN.md`——Initializer 产出 A，Gate 检查 B。

## 2. 决策

1. **唯一 Catalog**：新增内置默认 `artifact-catalog.yaml`（随 Runtime 分发），项目可用 `.codex-os/artifact-catalog.yaml` 覆盖；覆盖遵循与 Gate 规则相同的单调收紧校验——项目不得移除或放宽 baseline 必需 Artifact。每个 Artifact 以 pydantic 模型定义：id、canonical_path、phase、gate、producer、reviewers、required|optional、artifact_type。
2. **Canonical 路径保持现状**：`docs/PRODUCT_DESIGN.md`、`docs/INTERACTION_DESIGN.md`、`docs/UI_DESIGN.md` 为设计阶段必需 Artifact；`docs/design/UX_RESEARCH.md`、`docs/design/USER_FLOW.md`、`docs/design/WIREFRAME.md`、`docs/design/UI_SPEC.md` 降级为 optional 附属 Artifact（研究过程文档）。不迁移到 `docs/design/` 子目录，避免 G2/e2e/AGENT_HANDOFF 大面积重写。
3. **引用方全部改造**：`PHASE_DEFINITIONS` 与 `_input_artifacts_for` 从 catalog 编译；profiles 与 gates YAML 改用 artifact id 引用；模板键由 catalog 派生；e2e fixture 按 catalog 构造。治理代码与配置中禁止再出现硬编码 canonical 路径。
4. **校验命令**：新增 `codex-os artifact validate`——检查 id 唯一、canonical_path 唯一、gate/phase 引用有效、producer/reviewer 角色存在；接入 doctor 与 G1 检查。
5. **模板补齐**：初始化模板补齐 PRODUCT_DESIGN/INTERACTION_DESIGN/UI_DESIGN 三件套（由 catalog 驱动），保留 UX 四件套为可选附属模板；`docs/design/README.md` 与 `docs/README.md` 统一规则 5、`PROJECT_MASTER.md` 实现契约读取顺序改为引用 catalog。
6. **Schema 1.3**：Profile/Gate YAML 引用 artifact id 后，Profile Schema 随本 ADR 落地提升到 1.3（兼容读取 1.2/1.1/1.0）。

## 3. 被否决的选项

1. **迁移 canonical 路径到 `docs/design/`**：与 `docs/README.md` 统一规则 5 的目录约定一致，但破坏面覆盖 G2 检查、e2e、AGENT_SPEC/AGENT_HANDOFF 引用，收益仅目录美观；0.3.0 以收敛为纲，不做无功能收益的迁移。
2. **保留双模型并加映射层**：映射层即第二事实源，漂移仍会发生。
3. **把 UX 四件套提升为必需**：对纯后端项目强加 UX 流程，违反 Profile 增量能力原则。

## 4. 后果

- Artifact 事实只定义一次；六处引用点改为派生，Contract Drift 由 `artifact validate` 与漂移测试在 commit 前暴露。
- 初始化产出与 Gate 等待一致：新项目不再出现"Initializer 产 A、Gate 查 B"。
- `docs/design/` 保留为可选研究文档目录，AGENT_SPEC/AGENT_HANDOFF 的交接契约不受影响。

## 5. 验证

- `tests/unit/test_artifact_catalog.py`：catalog 加载、覆盖单调收紧、重复 id/路径与无效引用的失败路径。
- `codex-os artifact validate` 通过当前仓库与 fixture 项目。
- `tests/e2e/test_v11_governed_workflow.py` 改用 catalog 后保持通过。
- 全仓 grep 断言：治理代码与配置中无 `docs/PRODUCT_DESIGN.md` 等硬编码字面量（模板与 fixture 除外，且均由 catalog 派生）。
