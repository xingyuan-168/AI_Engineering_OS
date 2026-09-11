# AI Engineering OS

AI Engineering OS 是 Codex 的**工程治理层**：无状态三 Gate（Code Start / Frontend Approval / Finish）在任务开始、前端实现前、任务结束时回答"允许 / 不允许及为什么"。它不指导 Codex 怎么做专业工作——理解仓库、编码、调试、构建、测试、子 Agent 调度都是 Codex 的原生能力。

## 能力

- **GitHub 前置**：正式 src/ 实现前必须有可达的 GitHub remote；没有时允许读 input/、分析、调研、规划、写文档。
- **仓库卫生**：复制式版本目录/文件、被跟踪的污染内容、未解决冲突精确判定并阻塞；用户自己的未提交工作永不阻塞。
- **开源调研分层**：新项目/新模块/重大功能/新技术栈/新集成必须先在 docs/OPEN_SOURCE_RESEARCH.md 记录 requirement_id、summary 与 use / fork / extract / build 决策；空模板/stale id 一律阻塞。
- **前端人工确认**：新页面/新交互流/重大 UI 重构先出 docs/design/PROTOTYPE.html + UI_SPEC，用户批准作为 approval 块持久化进 UI_SPEC（scope 精确匹配）；文案/CSS/组件修复豁免。
- **Worktree 隔离**：disposable worktree 登记进 SQLite 供 Hook 判定（伪造目录失败封闭）；完成后 review、merge，cleanup 需 merge 证明，无 force。
- **轻量 Memory**：docs/memory/memory.jsonl 是唯一事实源（Git 跟踪），SQLite memory_index 可随时重建；单写者规则——子 Agent 只提交 candidate。
- **保护用户资产**：Hook 拦截 force push、删远端 ref、主工作区递归强删；pip/npm/sed -i 等正常工程命令全面放行。

## 本地开发

```powershell
uv sync
uv run ruff check src plugins tests
uv run pytest
uv run codex-os doctor --json
```

## 命令

```text
codex-os init <project-root> --project-id PROJECT-001 --name example
codex-os check <project-root> [--change-class --requirement-id]
codex-os finish <project-root> --test-command "pytest" --memory-not-needed
codex-os memory search|record|reindex|candidates|candidate --accept|--reject
codex-os worktree prepare|check|finish|cleanup|list
codex-os doctor
codex-os mcp
```

业务命令支持 --json（统一 ok/error envelope）。MCP 公开 8 个工具：project_init、governance_check、approval_record、context_refresh、worktree_manage、memory_search、memory_record、memory_candidate。

仓库级插件位于 plugins/ai-engineering-os/（8 个治理 Skill + SessionStart/PreToolUse Hooks）。Hook 属于纵深防御：客观边界（GitHub 就绪、伪造 worktree 封闭、无 GitHub 时禁写 src/）在 Hook 内离线强制，宿主可禁用；运行时入口检查仍是权威边界。

## 事实源

- [仓库指令](AGENTS.md)：十条宪法与 Git 纪律。
- [文档索引](docs/README.md)：全部活跃文档的地图与阅读顺序。
- [治理规则](docs/GOVERNANCE_RULES.md)：三 Gate、路径策略、Hook 语义。
- [架构决策](docs/ADR/)：已接受/已否决的重大决策（ADR-0016 为当前基线）。

input/ 保存原始需求，只读参考；冲突以当前接受的 ADR 为准。
