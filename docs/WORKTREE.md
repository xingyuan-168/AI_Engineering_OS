# Worktree 协议

AIOS 不创建、调度或管理 Agent；Codex 主会话负责规划与拆分，必要时调用原生子 Agent。AIOS 只负责把并行写隔离在登记的 disposable worktree 里。

## 四能力（core/worktree.py）

`prepare(task) / check(task) / finish(task) / cleanup(task)`（外加 list）。MCP `worktree_manage` 与 CLI `codex-os worktree ...` 暴露同一实现。

- Worktree 位于 `.worktrees/<slug>`，分支 `codex/wt-<slug>`，目标分支登记为 `target_branch`（默认 main）。
- `prepare` 登记进 SQLite worktrees 表；只有登记的真实 worktree（SQLite 记录 + cwd realpath 包含 + `git worktree list` 佐证）被 Hook 视为 disposable，伪造的 `.worktrees/` 目录失败封闭。
- disposable 区内：上下文感知命令放行；主工作区拦截。
- `finish` 要求 worktree 干净（全部已提交），否则拒绝——子 Agent 工作绝不丢失；finish 只把状态置为 `ready`（ready ≠ merged）。
- `cleanup` 必须先证明合并：目标分支无法解析、worktree 脏、或 `merge-base --is-ancestor <tip> <target>` 不成立（WORKTREE_NOT_MERGED）都拒绝；证明后删除 worktree 与分支并注销登记，无 `--force`。

## 纪律

- 并行写路径明显冲突的任务不得同时改；子 Agent 不写主工作区与其他 Worktree。
- 子 Agent 不写 `docs/memory/`：提交 candidate，主会话 Finish 时合并。
- 子 Agent 完成后回报变更、测试与风险；主会话 review 后合并，合并完成立即 cleanup。
- 不保留孤儿 Worktree；无 coordination/DAG/lease 机器。

## Handoff

轻量 handoff 规则：任务中断时在分支留下未完成说明（提交信息或 Worktree 内 NOTE），主会话可凭 worktree list + 分支状态恢复；不建独立的 handoff 文档体系。
