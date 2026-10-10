# Memory 契约

双层存储：Git 跟踪的 `docs/memory/memory.jsonl` 是唯一事实源；SQLite `memory_index` 表只是本机可重建的搜索索引（`codex-os memory reindex` 重建）。

## 记录类型与状态

- 类型：`decision` / `bug` / `lesson` / `pattern`，可选 `project-summary`。
- 状态：`active` / `superseded`（必须给出 superseded_by）/ `invalid`。

## 行格式

每行一个 JSON 对象：`id, type, title, summary, source, source_commit, tags, status, superseded_by`。写入时自动校验：类型/状态枚举、字段长度、source_commit 十六进制、Secret 模式拒绝、同 id 与同类型同标题去重。

录入、候选接受和重建索引共用校验，Secret 检查包括 tags。短临界区进程文件锁覆盖“读事实—校验—原子写入—重建索引”，防止并发丢记录。检索前刷新索引；JSONL 无效时阻塞，不静默返回旧索引。

## 单写者规则

- 子 Agent 不直接写 JSONL（Hook 在 disposable worktree 内拦截 docs/memory/ 写入）；用 `memory_record(candidate=true)` 或 `codex-os memory record --candidate` 提交 candidate 到 `.codex-os/state/memory-candidates/`。
- 主会话用 `codex-os memory candidate <id> --accept|--reject` 或 MCP `memory_candidate` 处理 candidate：accept 校验后并入 JSONL 并 reindex，reject 直接丢弃；Finish 时 Finish Gate 会提醒尚未处理的 candidate（非阻塞）。
- 接受失败保留 candidate 与事实；仅成功合入，或确认所有字段完全相同且已合入、索引刷新成功时才清理。仅标题相同不是可丢弃的重复；同 id 候选内容冲突也不覆盖。登记 Worktree 可以提交候选，但不能直接 record/accept/reject 主项目 Memory。

## 写入时机

只在重大决策完成、Bug 找到根因并修复、失败方案值得避免、形成可复用经验时写入。不记：每次命令、任务状态、聊天、推理过程、测试输出。
