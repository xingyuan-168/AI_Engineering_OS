# Frontend Gate

目的：真正开发 UI 前先看见、确认界面和交互，避免返工——不是生产 UX 文档。

## 流程

Requirement → User Flow（可写入 REQUIREMENTS/UI_SPEC）→ 交互式 HTML 原型 → UI Spec → 用户批准 → 实现。

## 正式产物（仅两个）

- `docs/design/PROTOTYPE.html`：单文件、免构建、浏览器可直接打开；覆盖需求点名的界面与交互状态。
- `docs/design/UI_SPEC.md`：布局、组件、状态与反馈规则。

## 分层适用

- 需要 Gate：新页面、新交互流程、重大 UI 重构。
- 豁免：改文案、修 CSS 错位、修既有组件 Bug。

## 批准

用户批准通过 `approval_record(gate="frontend", subject="frontend", decision="approved", decided_by=...)` 记录；随后 `governance_check(stage="frontend", frontend_impact=..., approved=true)` 放行。禁止推断或代填批准。拒绝则回到原型，不进入实现。
