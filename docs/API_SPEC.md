"""
# 接口契约

MCP 与 CLI 共享实现和业务响应封装（`{ok, data} / {ok:false, error:{code,message}}`）。项目参数允许根、子目录或登记 Worktree；共用解析器区分当前 checkout 与协调根。文件目标支持相对/绝对路径，并独立验证所属项目。无模型推理。

## MCP 工具（8）

| 工具 | 参数 | 语义 |
| --- | --- | --- |
| project_init | project_root, project_id?, name?, project_type?, include[]?, migrate_runtime? | 普通初始化必须提供 project_id/name；migrate_runtime=true 仅显式迁移运行库，不初始化文档/input；未知结构拒绝 |
| governance_check | project_root, stage=start\|frontend\|finish, change_class?, requirement_id?, frontend_impact?, frontend_scope?, test_command?, base_ref?, memory_written?, memory_not_needed?, remote?, run_id?, action=run\|status | Finish 必须传入任务起点 base_ref；status 仅 Finish，必须提供 run_id，不接受执行参数；保留 allowed/findings 完成响应，无旧布尔自证参数 |
| approval_record | project_root, gate, subject, scope?, decision=approved\|rejected, decided_by, reason? | 记录用户批准/拒绝；frontend gate 同时把 approval 块写入 docs/design/UI_SPEC.md（governance_check 读取该事实） |
| context_refresh | project_root | 重建 PROJECT_CONTEXT.md 派生缓存 |
| worktree_manage | project_root, action=prepare\|check\|finish\|cleanup\|list, name?, task_id?, base_ref? | disposable worktree 生命周期；cleanup 需合并证明，无 force |
| memory_search | project_root, query, limit | 从有效 JSONL 刷新索引后检索；事实无效时失败，不静默使用旧索引 |
| memory_record | project_root, record_type, title, summary, source, source_commit?, tags?, candidate? | 写 JSONL（主会话）或提交 candidate（子 Agent） |
| memory_candidate | project_root, action=list\|accept\|reject, candidate_id? | 主会话处理子 Agent candidate：列表 / 并入 JSONL / 丢弃 |

## CLI 命令（7 + 1 桥）

`codex-os init / check / finish / memory search|record|reindex|candidates|candidate / worktree prepare|check|finish|cleanup|list / mcp / doctor / authorize-hook`。

- `check [--change-class --requirement-id --remote]` = 只读配置预检、仓库治理、docs 检查与 Code Start 预览；阻塞退出码 40。
- `finish --base-ref <task-start-ref> [--change-class bugfix --remote <name> --run-id <UUID>] [--test-command "..."] --memory-written|--memory-not-needed` = Finish；检查完成但阻塞退出 40，成功 0，未完成或结构化预检错误 2，空仓库基线为 EMPTY_TREE。
- `finish <project-root> --status --run-id <UUID> --json` 只读取当前 checkout 的记录；无配置时已有记录仍可查询。不运行测试，不初始化、迁移、重建索引。查询成功不表示仓库通过。
- `init <project-root> --migrate-runtime` 只迁移已知旧库；见 DATABASE.md。正常 init 不向已有 input/ 补写 .gitkeep。
- `authorize-hook` = stdin JSON → 官方 Hook JSON；无阻塞输出 `{}`，拒绝只使用 deny，不使用 ask/allow 代替宿主授权。`--explain` 输出业务 envelope，其 data 含 layer=aios、decision、rule_id、targets、reason、next_step；不执行命令。
- Doctor 的 `ok: null` 表示未知（插件安装/Hook 信任/当前任务加载不能由本地文件证明）；`doctor --runtime-only --json` 仅报告当前 CLI 进程身份，不访问项目配置。MCP 每个响应的 meta.runtime 是该进程启动时固定的身份，磁盘包更新不改变旧进程报告。

## Finish 执行记录

`authorize-hook --explain` 的清理检查可返回 `CLEANUP_INSPECTION_TIMEOUT`；该结果阻断，保留精确目标和预算原因。`CLEANUP_TARGET_CHECKED` 仅证明本次路径检查通过，不证明任务归属或宿主批准。

`change_class` 默认为 null，MCP Start 必须显式指定；CLI `check` 不指定类型时只做预检，不代表 Code Start 通过。Finish 发现正式代码变化且未指定类型时阻断为 `CODE_START_UNVERIFIED`；纯文档允许省略。状态查询仍只接受根目录、stage、action、run_id，显式传入执行参数（包括 null/default 值）也拒绝。

适配层在长检查之前独占创建 `.codex-os/state/check-runs/<UUID>.json`，重复编号拒绝。分项开始、结束和最终结果原子写入，记录运行时身份、任务基线及解析提交、测试命令、时间、退出码和必要输出；没有新增工具、后台续跑或 SQLite 表。提供 MCP progressToken 可收到带编号的原生进度通知；调用方也可先提供 UUID。

状态为 running/completed/failed/timed_out/cancelled/interrupted/unavailable。completed 仅说明流程完成，通过还要求 decision.allowed=true。正常测试失败保留退出码并阻断。超时、取消、断连、工具不可用等未完成结果 decision=null，后续分项为 not_run；跳过项为 skipped，不冒充通过。测试默认超时 900 秒，Ruff 300 秒。

Windows 的命令等待加入 kill-on-close Job Object 并成功记录所属进程后才启动；其他支持平台使用独立进程组。超时/原生取消/断连中止所属进程树并确认退出。写入记录失败停止执行；查询失去所有者的 running 记录仅返回 interrupted，不写磁盘、不凭 PID 杀进程。完成记录不被迟到取消改写，也不是下一次 Gate 的通过缓存。

新记录使用 `record_version=2`，执行期间持有 `<UUID>.lock` 的系统文件锁；进程仍存在并不证明该次调用仍在运行。查询只读打开已有锁并作非阻塞探测；锁已释放时重新读取 JSON，优先返回已写入的终态，否则返回派生的 interrupted/decision=null，不改写历史。旧终态保持原义；旧 running 记录缺少调用锁证据，返回 unavailable/RUN_LIVENESS_UNVERIFIABLE。重复 UUID（包括遗留锁）拒绝。

创建前用 Git 实际规则确认记录、调用锁和目录互斥锁都未被跟踪且被忽略，反向/嵌套规则同样适用；失败返回 RUN_PATH_NOT_IGNORED 或 GIT_IGNORE_CHECK_FAILED，不创建记录、不修改忽略配置、不启动测试。历史查询不依赖当前配置或忽略规则。写入错误保留异常类型、errno/winerror、路径和原始原因。

Ruff 配置优先级为 `.ruff.toml`、`ruff.toml`、含 `tool.ruff` 的 `pyproject.toml`（子表也算）；使用 TOML 解析。仅未配置时 RUFF_SKIPPED，非法配置为 RUFF_CONFIG_INVALID，不可读/工具缺失为 RUFF_UNAVAILABLE。Git 空白检查的 findings.details 保存 committed/staged/unstaged、exit_code、stdout、stderr；发现问题为 GIT_DIFF_CHECK，命令未完成为 GIT_DIFF_CHECK_FAILED，不从冒号猜路径。

## 错误码

配置类 CONFIG_MISSING/CONFIG_UNREADABLE/CONFIG_INVALID/CONFIG_RUNTIME_MISMATCH；路径与原始异常在 details 中单独保存，CLI/MCP 不从异常冒号猜代码。其他运行异常不归为配置错误。Gate findings 包含 code/message/path/blocking/details；Memory 类 MEMORY_*；Worktree 类 WORKTREE_*；数据库类 MIGRATION_*；执行类 RUN_* 与 FINISH_*。
