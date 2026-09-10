# 变更记录

## [1.0.0] - 2026-09-10

### governance-core 轻量化大重构（ADR-0016，breaking change）

版本定稿：Dogfood 四案例全部通过（无 GitHub 阻塞/放行、正常后端流程、前端批准与豁免、子 Agent worktree 隔离与清理），89 项测试全绿，复杂度预算达标（MCP 7/8、CLI 7+1/8、Skills 8/9、活跃 docs 13/10~15、Gate 3、SQLite 5/6 表）。

## Unreleased

### governance-core 轻量化大重构（ADR-0016，breaking change）

- 删除：G0-G4 状态机与五层 Gate、Release/G4、Verification Cache、Evidence、Host Operation、自有执行平台（Docker/Podman 适配器）、Artifact Catalog、coordination 三层、0016 前的 61 份文档体系、21 个 Skills、SQLite 迁移链 0001~0008、ERP pilot。
- 新增：无状态三 Gate 评估器（core/gates.py）、单迁移轻量 SQLite（tasks/approvals/worktrees/memory_index 四表 + 旧库导出保护）、JSONL 单写者 Memory（docs/memory/memory.jsonl + 可重建 memory_index）、disposable worktree 生命周期（core/worktree.py）、最小项目文档模板、治理 Hook 语义重写（保护用户资产 + Memory 单写者）。
- 收敛：MCP 30→7 工具；CLI 12→7 命令（+authorize-hook 桥）；Skills 21→8；文档 61→约 14 活跃。
- 版本号待 Phase 6 Dogfood 通过、用户确认后一次性定稿。
