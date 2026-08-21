# Worktree 规范

版本：V2.0-derived-worktree
状态：可执行实现规格基线

## 1. 创建条件

满足任一条件时创建独立 Worktree：复杂度评分达到多 Agent 阈值、任务需要并行修改、任务风险要求隔离、或用户明确要求角色分工。简单只读分析和单文件文档任务可使用主工作树，但不得与活动 Agent 冲突。

## 2. 目录和 Branch

```text
.worktrees/
  <workflow-id>/
    <agent-name>/
      <task-id>/
```

Branch 固定为 `agent/<agent-name>/<task-id>`。Workflow、Agent、Task、Branch 和 Worktree 路径必须一一关联并写入事件日志。

## 3. 创建前检查

- Git 仓库和目标提交有效。
- Branch 名称未被占用。
- Worktree 目标路径不存在且不在其它 Worktree 内。
- 任务 `read_paths`/`write_paths` 已计算。
- 当前主工作树不存在未确认的冲突修改。
- 磁盘空间和容器挂载条件满足。

任一检查失败则任务进入 `blocked`，不得强行创建。

## 4. Agent 权限

- Agent 只能修改当前 Worktree 和任务允许路径。
- 禁止直接写主工作树或其他 Agent Worktree。
- 禁止通过符号链接、路径拼接或容器挂载绕过路径边界。
- 分支提交必须包含任务 ID，禁止提交未关联的临时产物。

## 5. 合并流程

1. Agent 完成修改并运行声明的测试。
2. 生成变更摘要、文件列表、Commit、测试和风险记录。
3. 创建 `AGENT_HANDOFF.md` 交接包并请求 Reviewer。
4. Reviewer 审阅并验证产物 hash、测试和路径。
5. 通过后合并到目标分支并记录合并 Commit。
6. 冲突、脏工作树、测试失败或证据缺失时进入 `blocked`，禁止自动覆盖。

## 6. 清理规则

- 完成任务保留 Branch、Commit、交接包和审计记录。
- 清理前确认没有未提交变更、未交付产物或待处理 Review。
- 失败任务保留现场，至少直到人工确认恢复或放弃。
- 孤儿 Worktree 只能在列出路径、分支、最后事件并人工确认后回收。
- 回收操作不得删除 Git 历史和 SQLite 审计记录。

## 7. 完成定义

Worktree 规范只有在创建条件、目录、Branch、权限、合并、冲突、清理和失败保留均有验证场景时才算完成。
