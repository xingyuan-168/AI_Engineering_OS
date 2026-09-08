# ADR-0014：Assurance 分层——复用 RiskLevel 的差异化保障要求

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.3.0","status":"accepted","owner":"architect","requirement_refs":["GATE-001","EXEC-001","VERSION-001"]} -->

- 状态：Accepted
- 日期：2026-09-08
- 决策版本：0.3.0 / API 1.2 / SQLite 0008
- 来源：`docs/proposals/AI-OS_v0.3_Governance_Consolidation_改进方案.md` P1-02（经 0.3.0 源码核对修正）

## 1. 上下文

0.3.0 治理核对确认：

1. Runtime 已有四级 `RiskLevel`（low/medium/high/critical，`domain/config.py`）、阶段级与动作级风险（`PhaseDefinition.risk_level`、`SandboxRequest.risk_level`）与 `approval_for` 分类审批，但 Gate 证据要求与检查集是固定的：`gates/G3.yaml` 对任何变更要求 17 项检查，修改文档与修改认证模块的成本近似。
2. 全仓不存在 Assurance Level 或按风险分层的检查集；Profiles 亦无风险维度。

## 2. 决策

1. **不新建平行枚举**：Assurance 分层复用既有 `RiskLevel` 派生，避免第二套风险模型。分层语义如下（映射表数据驱动，随 Gate 规则分发）：
   - L0（对应 low、纯文档/注释/格式）：diff 校验 + 基础 lint（ruff、docs、secret-scan）。
   - L1（小型缺陷修复、非关键调整）：聚焦测试 + lint + review。
   - L2（常规 feature）：L1 + 全量单测/集成测试 + G1-G3 完整证据。
   - L3（auth/permission/database/payment/security/infra 关键路径）：L2 + security scan（bandit）、dependency audit、migration 验证 + G0-G4。
   - L4（production release、关键基础设施、高风险部署）：L3 + SBOM、OCI 证据、release candidate、rollback 证据、artifact hash（即现行 G4 全量要求）。
2. **派生规则**：Run 的 `risk_level` 决定基线分层；任务 impact 路径命中关键路径关键词（auth、permission、database、payment、security、infra、migration）时，最低分层提升到 L3。用户与 Profile 只能提升，不能降低系统确定的最低分层（沿用 `_require_monotonic_gate` 的单调收紧模式）。
3. **落地形态**：`gates/*.yaml` 增加按分层的检查集映射（如 `assurance: {L0: [...], L1: [...]}`），`governance_policy.compile()` 按 Run 风险与关键词解析出有效检查集并写入 `EvidenceRequirements`；Gate 引擎按有效集合评估，Python 侧不再按 gate/阶段硬编码检查组合。
4. **范围守护**：本分层不新增 Gate、不新增 Workflow、不新增 Agent；只调整既有检查集的组合方式。

## 3. 被否决的选项

1. **新建 L0-L4 独立枚举**：与 `RiskLevel` 并行形成第二套风险事实源，正是提案第 13 节禁止的设计。
2. **在 Profile 中手工声明 assurance 字段**：人工维护必然漂移，应由风险 + 路径派生。
3. **按 Gate 硬编码分层分支**：重回数据驱动的反面。

## 4. 后果

- 修改 README/文档的 Run 不再触发全量 17 项检查；auth/db/release 变更自动提升到 L3/L4。
- 关键路径关键词清单集中在数据文件中，可审计、可测试。
- `TEST_PLAN.md` 与 `WORKFLOW_SPEC.md` 的检查矩阵章节随本 ADR 更新，说明分层与最低保障语义。

## 5. 验证

- `tests/unit/test_assurance_levels.py`：风险→分层映射、关键词提升、单调收紧（拒绝降低）。
- `tests/e2e/test_v11_governed_workflow.py` 保持通过（高风险 Run 仍获全量检查集）。
- 新增 e2e 低风险 Run fixture：文档类变更仅要求 L0 检查集即可过 Gate。
