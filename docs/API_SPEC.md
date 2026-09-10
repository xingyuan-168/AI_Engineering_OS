# 接口契约

MCP 与 CLI 共享同一实现与响应封装（`{ok, data} / {ok:false, error:{code,message}}`）。全部为确定性治理用例，无模型推理。路径输入为项目根相对路径。

## MCP 工具（7）

| 工具 | 参数 | 语义 |
| --- | --- | --- |
| project_init | project_root, project_id, name, project_type, risk_level, include[] | 幂等初始化：配置 + 最小文档 + 运行库；include 可选 api/database/test_plan/security/deployment/frontend_design/docker |
| governance_check | project_root, stage=start\|frontend\|finish, change_class?, research_done?, frontend_impact?, approved?, tests_passed?, docs_synced?, memory_written?, memory_not_needed? | 无状态三 Gate 裁决，返回 allowed + findings |
| approval_record | project_root, gate, subject, decision=approved\|rejected, decided_by, reason? | 记录用户批准/拒绝（frontend gate 由 governance_check 读取最新记录） |
| context_refresh | project_root | 重建 PROJECT_CONTEXT.md 派生缓存 |
| worktree_manage | project_root, action=prepare\|check\|finish\|cleanup\|list, name?, task_id?, base_ref?, force? | disposable worktree 生命周期 |
| memory_search | project_root, query, limit | 检索 memory_index |
| memory_record | project_root, record_type, title, summary, source, source_commit?, tags?, candidate? | 写 JSONL（主会话）或提交 candidate（子 Agent） |

## CLI 命令（7 + 1 桥）

`codex-os init / check / finish / memory search|record|reindex|candidates / worktree prepare|check|finish|cleanup|list / mcp / doctor / authorize-hook`。

- `check` = 仓库治理（GitHub 就绪 + 卫生 + output 纯净）+ docs 检查，合并报告。
- `finish --tests-passed --docs-synced --memory-written|--memory-not-needed` = Finish Gate；退出码 40 表示阻塞。
- `authorize-hook` = Codex PreToolUse Hook 的内核裁决桥（stdin JSON → hook JSON），裁决失败退出码非 0，由 hook 走退化规则。

## 错误码

配置类 CONFIG_INVALID；Gate 类返回 findings（code/message/path/blocking），决策本身不报错；Memory 类 MEMORY_*；Worktree 类 WORKTREE_*；数据库类 MIGRATION_*。
