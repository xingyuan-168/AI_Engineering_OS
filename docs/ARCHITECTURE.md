# 架构

Windows 本地、可审计的 Codex 工程治理层。Python 3.12，自研自持（Gate、审批、SQLite），无第二模型客户端；LangGraph 等仅作设计参考，禁止作为依赖引入。

## 分层

- `cli/` — Typer 命令（init/check/finish/memory/worktree/mcp/doctor/authorize-hook）与 MCP stdio server（8 工具）。输出统一 ok/error envelope。
- `application/` — 用例：project 初始化、repository 治理检查、doctor 诊断、hook 网关、授权内核（ADR-0011）、最小路径策略内核（governance_policy）。
- `core/` — 无状态治理核心：`gates.py`（三 Gate 评估器）、`checks.py`（Finish 薄真实检查）、`worktree.py`（disposable worktree 生命周期）。
- `infrastructure/` — SQLite（单迁移 0001：tasks/approvals/worktrees/memory_index）、JSONL Memory、文档管理、配置、路径编解码。
- `adapters/` — 外部系统：GitRunner（唯一 Git 子进程封装）。
- `domain/` — 配置模型、治理值对象、版本常量。
- `templates/` — 新项目最小文档集（baseline + 条件生成）。
- `plugins/ai-engineering-os/` — Codex 插件：8 个治理 Skills、SessionStart/PreToolUse hooks、MCP 启动脚本。

## 边界

- Markdown/Git 保存项目事实；SQLite 保存运行状态与可重建索引；`.codex-os/` 是运行状态目录（project.yaml、state.db、派生 context），全部 Git 忽略。
- AIOS 暴露确定性 CLI/MCP 用例，永远不做模型推理、不做 Agent/工具运行时、不做 DAG 调度。
- Docker/Podman 不是 AIOS 的执行引擎：project_init 仅提供可选的项目开发环境模板（Dockerfile + compose.yaml）。
- Hook 失败是 best-effort：宿主可禁用 hook，运行时入口检查才是权威边界；CLI 授权裁决失败时 hook 走退化规则（fail-closed 信号）。
