# AI Engineering OS

AI Engineering OS 是面向 Codex 的 Windows 本地工程工作流运行时。它把需求、研究、设计、实现、验证、发布和记忆组织为可审批、可恢复、可审计的状态机，并以 Git 提交和制品哈希作为交付证据。

## 当前目标

首个纵向切片实现 `new-project`：从空 Git 仓库和业务目标开始，经过 G0-G4 门禁，在隔离 worktree 与 Docker 沙箱中完成一个 FastAPI + SQLite ERP 后端发布候选。

当前分支处于实现阶段；可运行 CLI、插件和试点将在对应里程碑提交中逐步加入。

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
