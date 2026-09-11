# 变更记录

## [1.0.0] - 2026-09-10

### governance-core 轻量化大重构（ADR-0016，breaking change）

版本定稿：Dogfood 四案例全部通过（无 GitHub 阻塞/放行、正常后端流程、前端批准与豁免、子 Agent worktree 隔离与清理），当次基线环境验证通过，复杂度预算达标（MCP 7/8、CLI 7+1/8、Skills 8/9、活跃 docs 13/10~15、Gate 3、SQLite 5/6 表）。

## Unreleased

### governance-core 审计修复（fix/governance-hardening）

- fix(gates): Code Start 强制 GitHub remote + 复制式脏乱判定；开源调研文档要求 requirement_id 开头并给出 summary 与 Decision/reason，空模板/stale id/缺 reason 均阻塞，无布尔绕过。
- fix(frontend): 批准事实持久化为 docs/design/UI_SPEC.md 的 `approval:` 块（scope 精确匹配），删除调用方 approved 布尔绕过。
- fix(worktree): cleanup 先证明合并（`merge-base --is-ancestor <tip> <target>`），脏树与未合并一律拒绝；finish 置 ready（ready ≠ merged）；force 参数全面删除；Hook 只信任登记的真实 worktree，伪造 .worktrees/ 失败封闭，temp 判定跨平台。
- refactor(auth): 授权内核只判操作不判角色（principal/TRANSITION/policy_hash/RoleBoundary/GovernanceMode 删除）；output/ 可写，其纯净由卫生检查判定；OCI 话术改为"可能破坏项目持久数据"语义。
- fix(finish): 删除 --tests-passed/--docs-synced 自证；新增 core/checks.py 薄真实检查（声明的 --test-command、配置了 ruff 才跑 ruff、git diff --check、卫生、candidate 提醒非阻塞）。
- fix(memory): candidate 闭环——accept 校验并入/reject 丢弃/未知 id MEMORY_CANDIDATE_MISSING；CLI memory candidate 与 MCP 第 8 工具 memory_candidate。
- refactor(config): ProjectConfig 只保留运行时读取的字段（project_type 驱动模板，新增 code_paths），risk_level/环境/执行策略字段与 .codex/agents 角色档案删除。
- chore(repo): 删除 .codex-os/gates、environment.yaml、execution-policy.yaml、test-traceability.yaml；secret 扫描脚本晋升 scripts/secret_scan_incremental.py；.gitignore 合规检查（15 项运行时产物，等价写法允许）。
- docs: ADR 0001/0003/0009 随被删运行时退役；AGENTS.md/README/API_SPEC/GOVERNANCE_RULES/WORKTREE/MEMORY/DATABASE/TEST_PLAN/ARCHITECTURE 同步。

### governance-core 第二轮审计加固（fix/aios3-hardening）

- fix(gates): 开源调研事实增加 scope（行内/块列表）与 updated_at（YYYY-MM-DD）必填校验。
- feat(finish): Code Start 第二层复核——未提交/已暂存改动触及 code_paths 时必须携带 --change-class（研究类另需 --requirement-id）复跑 Code Start，间接写入（脚本生成源码）在完成时被拦截；无状态设计不变。
- fix(frontend): approval 块持久化 approved_by（decided_by 传递），SQLite 仍只是索引。
- docs(governance): AGENTS.md 收敛为十条宪法 + Git 节奏 + 原则化护栏，验证与实现边界细节下沉 GOVERNANCE_RULES；ADR-0010/0011/0015 标注部分 Superseded（只改状态行，不改历史正文）。
- chore(repo): 删除 .codex-os/tmp 一次性编辑脚本与本地缓存残留。
