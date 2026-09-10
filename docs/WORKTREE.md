# Worktree 协议

AIOS 不创建、调度或管理 Agent；Codex 主会话负责规划与拆分，必要时调用原生子 Agent。AIOS 只负责把并行写隔离在登记的 disposable worktree 里。

## 四能力（core/worktree.py）

`prepare(task) / check(task) / finish(task) / cleanup(task)`（外加 list）。MCP `worktree_manage` 与 CLI `codex-os worktree ...` 暴露同一实现。

- Worktree 位于 `.worktrees/<slug>`，分支 `codex/wt-<slug>`，基于 `--base-ref`（默认 HEAD）。
- `prepare` 登记进 SQLite worktrees 表，供 Hook 路径判定为 disposable。
- disposable 区内：上下文感知命令放行；主工作区拦截。
- `finish` 要求 worktree 干净（全部已提交），否则拒绝——子 Agent 工作绝不丢失。
- `cleanup` 删除 worktree 与分支并注销登记；名称/路径/分支随即复用。

## 纪律

- 并行写路径明显冲突的任务不得同时改；子 Agent 不写主工作区与其他 Worktree。
- 子 Agent 不写 `docs/memory/`：提交 candidate，主会话 Finish 时合并。
- 子 Agent 完成后回报变更、测试与风险；主会话 review 后合并，合并完成立即 cleanup。
- 不保留孤儿 Worktree；无 coordination/DAG/lease 机器。

## Handoff

轻量 handoff 规则：任务中断时在分支留下未完成说明（提交信息或 Worktree 内 NOTE），主会话可凭 worktree list + 分支状态恢复；不建独立的 handoff 文档体系。
