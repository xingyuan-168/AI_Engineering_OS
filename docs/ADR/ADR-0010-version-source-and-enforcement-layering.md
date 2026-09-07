# ADR-0010：版本事实源贯通、强制力分层与状态词汇澄清

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.1","status":"accepted","owner":"architect","requirement_refs":["REQ-1.6.2","VERSION-001","GOV-001","DOC-001"]} -->

- 状态：Accepted
- 日期：2026-09-07
- 决策版本：0.2.1 / API 1.2 / SQLite 0008
- 澄清：ADR-0004 §2.1、ADR-0005 §2 的散文落地缺口；SCOPE §1 的强制力宣称

## 1. 上下文

0.2.1 治理审计发现三类残留问题：

1. `RUNTIME_VERSIONS`（ADR-0009 §1）已在代码与测试侧成为唯一版本事实源，但活跃文档散文仍复制版本事实：SQLite `0007`、软件 `0.2.0`、tag `v0.2.0` 等旧值散落在 PROJECT_MASTER、ARCHITECTURE、BUSINESS_RULES、TECH_STACK、TEST_PLAN、RELEASE_CHECKLIST、RELEASE_CLOSURE_MATRIX、CHANGELOG、SCOPE、SECURITY 与 DATABASE 中，且无测试守卫。
2. `SCOPE.md` §1 宣称"关键规则不依赖提示词或人工自觉"，与 `SECURITY.md` §8 的诚实边界（Hook 仅防御纵深，Runtime 结构化管控只约束经过 Runtime 的调用）口径不一致，顶层宣称过强。
3. `AGENTS.md`、`SCOPE.md` 与 `CHANGELOG.md` 使用"workflow 保持 blocked 或 reconcile_required"的表述，而 `reconcile_required` 是 Host Operation 状态，不是 `run_status` 枚举；两套状态词汇在文档中混用且无对照定义。

## 2. 决策

1. **版本事实源贯通**：活跃文档的版本事实一律从 `RUNTIME_VERSIONS` 派生。散文版本矩阵的唯一权威位置是 `PROJECT_MASTER.md` 版本矩阵节；BUSINESS_RULES、TEST_PLAN、TECH_STACK、RELEASE_CHECKLIST、RELEASE_CLOSURE_MATRIX、ARCHITECTURE、DATABASE、SECURITY 与 CHANGELOG 的 `Unreleased` 段必须与该矩阵一致。已发布的历史 ADR（0001-0009）与 CHANGELOG 历史版本节保持冻结，不作回写。`tests/unit/test_spec_consistency.py` 增加动态断言与 stale 模式反向断言，使未来漂移直接 fail 测试。
2. **强制力分层声明**：顶层宣称统一为——"关键规则由 Runtime 代码强制执行（结构化 argv、路径校验、审批、证据、乐观锁与沙箱策略）；宿主 PreToolUse Hook 是防御纵深，不是权限、路径或命令安全边界"。`SCOPE.md` §1 按此改写，与 `SECURITY.md` §8 对齐。
3. **状态词汇对照**：`workflow_phase`（业务进度）、`run_status`（执行生命周期：`created/running/needs_approval/paused/blocked/failed/cancelled/completed`）与 Host Operation 状态（`pending/running/succeeded/failed/reconcile_required`）是三个独立词汇表。`WORKFLOW_SPEC.md` 增加对照表；`AGENTS.md`、`SCOPE.md`、`CHANGELOG.md` 中"workflow 保持 blocked 或 reconcile_required"的表述修正为"Workflow 保持 `blocked`，相关 Host Operation 保持 `reconcile_required`"。
4. **默认沙箱口径**：以 ADR-0007 为准——Docker 与 Podman 双适配，新项目默认 Podman，禁止静默回退。`CHANGELOG.md` 旧条目中的"默认仍为 Docker"按历史记录保留，但本 ADR 明确其已被 ADR-0007 取代。
5. **G3 证据绑定**：以 ADR-0009 §3 为准（目标 Commit 或祖先 + 内容复验；`html-prototype-validator` 与 `ux-prototype` 精确相等）。`SECURITY.md` §7 中"任何后续 merge 使 G3 失效"的旧表述按此修正；实现已在 `infrastructure/evidence.py` 完成。

## 3. 被否决的选项

1. **把散文矩阵全部删除只留代码**：丢失用户可 Review、可 diff 的治理契约（同 ADR-0005 §4.2）。
2. **回写历史 ADR 的版本值**：历史决策记录不可改写，回写破坏审计链。
3. **把 `reconcile_required` 加入 `run_status`**：会混淆执行生命周期与副作用对账语义，破坏双轴状态设计。

## 4. 后果

- 版本升级只改 `RUNTIME_VERSIONS`、`PROJECT_MASTER.md` 矩阵节与受影响测试；其余文档由测试守卫强制同步，漂移在 commit 前失败。
- 文档与安全的强制力口径一致，安全边界评估不再依赖顶层宣称。
- 三个状态词汇表各有明确定义位置，跨文档引用可复算。

## 5. 验证

- `test_spec_consistency.py` 的版本矩阵动态断言与 stale 模式反向断言通过。
- `test_workflow_models.py` 的 `RunStatus` 枚举与 WORKFLOW_SPEC 对照表断言一致。
- `check-docs` 全量通过，无 `VERSION_REFERENCE_STALE`。
