# Architecture Decision Records

本目录保存已接受或已拒绝的重大技术决策、替代方案、证据、后果和后续复核条件。

当前有效决策：

- [ADR-0005：治理权威、规格一致性与自修改规则](ADR-0005-governance-authority-and-specification-consistency.md)
- [ADR-0010：版本事实源贯通、强制力分层与状态词汇澄清](ADR-0010-version-source-and-enforcement-layering.md)
- [ADR-0011：统一治理授权内核与三层强制模型](ADR-0011-governance-authorization-kernel.md)
- [ADR-0015：Git Push Checkpoint 策略](ADR-0015-push-checkpoint-policy.md)
- [ADR-0016：治理核心轻量化大重构](ADR-0016-governance-core-lightweight-refactor.md)

已删除的 ADR（0002、0004、0006、0007、0008、0012、0013、0014）随 ADR-0016 的废除决定移除，0001/0003/0009 因描述被删除的执行运行时与 G0-G4 证据体系随审计修复移除；Git 历史是唯一归档，需要时从历史恢复。

部分 Superseded 标注（0010/0011/0015）只改状态行，不改历史正文：保留仍有效的结论，失效小节在状态行内说明并被 ADR-0016 取代；Codex 以状态行为准，不按失效小节执行。

新增 ADR 必须使用唯一编号，记录状态、上下文、选项、决定和后果；不得覆盖既有历史。
