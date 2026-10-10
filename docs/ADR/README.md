# Architecture Decision Records

本目录保存已接受或已拒绝的重大技术决策、替代方案、证据、后果和后续复核条件。

当前有效决策：

- [ADR-0005：治理权威、规格一致性与自修改规则](ADR-0005-governance-authority-and-specification-consistency.md)
- [ADR-0010：版本事实源贯通、强制力分层与状态词汇澄清](ADR-0010-version-source-and-enforcement-layering.md)
- [ADR-0011：统一治理授权内核与三层强制模型](ADR-0011-governance-authorization-kernel.md)
- [ADR-0015：Git Push Checkpoint 策略](ADR-0015-push-checkpoint-policy.md)
- [ADR-0016：治理核心轻量化大重构](ADR-0016-governance-core-lightweight-refactor.md)
- [ADR-0017：目标路径、数据保全与失败语义](ADR-0017-safe-boundaries-and-failure-semantics.md)
- [ADR-0018：门禁来源判定与 Finish 执行诊断](ADR-0018-gate-diagnostics-and-provenance.md)

已删除的 ADR（0002、0004、0006、0007、0008、0012、0013、0014）随 ADR-0016 的废除决定移除，0001/0003/0009 因描述被删除的执行运行时与 G0-G4 证据体系随审计修复移除；Git 历史是唯一归档，需要时从历史恢复。

部分 Superseded 标注只改状态行，不改历史正文：0016 保留轻量架构基线，0017 覆盖清理/失败/迁移/验收细节。0005 中旧 runtime/审批凭证和 0015 的旧 push 节奏不再作为当前要求；当前节奏以 AGENTS.md 为准。Codex 不按失效历史小节执行。

新增 ADR 必须使用唯一编号，记录状态、上下文、选项、决定和后果；不得覆盖既有历史。
