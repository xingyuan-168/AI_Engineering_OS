# Memory 契约

双层存储：Git 跟踪的 `docs/memory/memory.jsonl` 是唯一事实源；SQLite `memory_index` 表只是本机可重建的搜索索引（`codex-os memory reindex` 重建）。

## 记录类型与状态

- 类型：`decision` / `bug` / `lesson` / `pattern`，可选 `project-summary`。
- 状态：`active` / `superseded`（必须给出 superseded_by）/ `invalid`。

## 行格式

每行一个 JSON 对象：`id, type, title, summary, source, source_commit, tags, status, superseded_by`。写入时自动校验：类型/状态枚举、字段长度、source_commit 十六进制、Secret 模式拒绝、同 id 与同类型同标题去重。

## 单写者规则

- 子 Agent 不直接写 JSONL（Hook 在 disposable worktree 内拦截 docs/memory/ 写入）；用 `memory_record(candidate=true)` 或 `codex-os memory record --candidate` 提交 candidate 到 `.codex-os/state/memory-candidates/`。
- 主会话在任务 Finish 时统一合并 candidate 并 reindex。

## 写入时机

只在重大决策完成、Bug 找到根因并修复、失败方案值得避免、形成可复用经验时写入。不记：每次命令、任务状态、聊天、推理过程、测试输出。
