# AI Engineering OS

AI Engineering OS 是面向 Codex 的 Windows 本地工程工作流运行时。它把需求、研究、设计、实现、验证、发布和记忆组织为可审批、可恢复、可审计的状态机，并以 Git 提交和制品哈希作为交付证据。

## 当前目标

首个纵向切片实现 `new-project`：从空 Git 仓库和业务目标开始，经过 G0-G4 门禁，在隔离 worktree 与 Docker 沙箱中完成一个 FastAPI + SQLite ERP 后端发布候选。

当前已具备 Python 3.12 包、严格配置、SQLite 迁移、追加式事件、项目初始化、文档治理、`new-project` 状态机、Codex 私有插件和 stdio MCP 主路径。沙箱执行和 ERP 试点在后续里程碑加入。

## 本地开发

```powershell
uv sync --frozen --all-groups
uv tool install --editable .
uv run ruff check .
uv run pyright
uv run pytest --cov=codex_ai_os
uv run codex-os doctor --json
```

当前命令：

```text
codex-os doctor
codex-os init <project-root> --project-id PROJECT-001 --name example
codex-os status <project-root>
codex-os check-docs <project-root>
codex-os run new-project --goal "开发 ERP 采购模块" --project-root <project-root>
codex-os step <run-id> --project-root <project-root>
codex-os approve <run-id> --gate G0 --reason "范围已确认" --project-root <project-root>
codex-os reject <run-id> --gate G0 --reason "范围需补充" --project-root <project-root>
codex-os resume <run-id> --project-root <project-root>
codex-os mcp
```

除作为协议进程运行的 `mcp` 外，业务命令支持 `--json`；启用后 stdout 只包含统一 JSON 响应，日志和面向用户的说明不混入 stdout。

仓库级私有插件位于 `plugins/ai-engineering-os/`，marketplace 位于 `.agents/plugins/marketplace.json`。先以 uv 安装 `codex-os` runtime，插件的 Windows 启动器会从 `PATH` 或 uv 默认用户目录启动 stdio 服务。MCP 公开 `project_init`、Workflow 控制、审批、`task_complete`、文档检查、验证、发布候选和记忆检索工具。

插件捆绑 11 个工程 Skill 和 SessionStart/PreToolUse Hooks；项目级 `.codex/agents/` 定义产品、架构、后端、数据库、QA、安全和 Reviewer profile。插件 Hook 属于非托管 Hook，安装或变更后必须通过 `/hooks` 复核并信任其当前 hash，不能把 Hook 当作唯一安全边界。

## 事实源

- [执行入口](<AI_Engineering_OS_Codex_执行入口文档（1）(2).md>)：文档读取和项目启动顺序。
- [项目总文档](docs/PROJECT_MASTER.md)：目标、范围与治理总览。
- [系统架构](docs/ARCHITECTURE.md)：组件边界和数据流。
- [实施规格索引](docs/README.md)：全部领域规范。
- [仓库指令](AGENTS.md)：Codex 实现、验证和 Git 纪律。

`input/` 保存原始需求，只作为参考；冲突以当前接受的 ADR 和领域规格为准。

## 目标架构

```text
Codex Host / Plugin
        |
      MCP stdio
        |
Python CLI + application services
        |
Workflow / Approval / Documents / Git Evidence
        |
SQLite state + Markdown/Git facts + Docker execution
```

## Git 规则

远端为 `git@github.com:xingyuan-168/AI_Engineering_OS.git`。每个完整逻辑变更独立提交，验证后立即推送当前里程碑分支；禁止 force push 和改写已发布历史。

## 支持边界

- Windows 本地运行，Python 3.12。
- Docker Desktop 是写操作和高风险执行的默认沙箱。
- Codex Host/MCP 是模型编排主路径；`codex exec` 仅为环境验证后的可选叶子适配器。
- V1 不包含 Web 控制台、DeepSeek Harness、公共插件发布或远程多租户服务。
