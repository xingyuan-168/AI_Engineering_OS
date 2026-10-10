# 架构

Windows 本地、可审计的 Codex 工程治理层。Python 3.12，自研自持（Gate、审批、SQLite），无第二模型客户端；LangGraph 等仅作设计参考，禁止作为依赖引入。

## 分层

- `cli/` — Typer 命令（init/check/finish/memory/worktree/mcp/doctor/authorize-hook）与 MCP stdio server（8 工具）。输出统一 ok/error envelope。
- `application/` — 用例：project 初始化、repository 治理检查、配置预检、Finish 执行诊断、doctor、hook 网关、授权内核及最小路径策略。
- `core/` — 无状态治理核心：`gates.py`（三 Gate 评估器）、`checks.py`（Finish 薄真实检查）、`worktree.py`（disposable worktree 生命周期）。
- `infrastructure/` — SQLite（单迁移 0001：tasks/approvals/worktrees/memory_index）、JSONL Memory、文档管理、配置、路径编解码。
- `adapters/` — 外部系统：GitRunner（唯一 Git 子进程封装）、所属外部命令树控制（Windows Job Object / POSIX 进程组）。
- `domain/` — 配置模型、治理值对象、版本常量。
- `templates/` — 新项目最小文档集（baseline + 条件生成）。
- `plugins/ai-engineering-os/` — Codex 插件：8 个治理 Skills、SessionStart/PreToolUse hooks、MCP 启动脚本。

## 边界

- Markdown/Git 保存事实；`.codex-os/project.yaml` 是 Git 管理的配置，state/context 等运行状态忽略。SQLite 普通访问不重建；显式迁移使用一致性备份和 SQL 事务。JSONL 写锁与原子替换保护 Memory，索引永不替代事实。
- AIOS 暴露确定性 CLI/MCP 用例，永远不做模型推理、不做 Agent/工具运行时、不做 DAG 调度。
- Docker/Podman 不是 AIOS 的执行引擎：project_init 仅提供可选的项目开发环境模板（Dockerfile + compose.yaml）。
- Hook 的 stdlib 适配器调用单一 Runtime 授权内核；明确写入/破坏操作遇到缺失、超时或无效结果输出 deny，其他操作报告未知，不复制离线内核。共享根解析和实际目标检查覆盖子目录/Worktree/跨 cwd 路径。宿主可禁用 Hook，也可独立拒绝 AIOS 放行的操作；诊断不冒充宿主审批。任务基线由原生上下文携带到 Finish，不引入任务凭证/调度状态。
- 运行入口只选择明确绝对入口 CODEX_OS_RUNTIME、仓库开发环境、PATH 的 codex-os，先核对产品/API/配置及发布构建指纹，不静默调用独立 aios。CLI、MCP、Hook 共用选择器；同一次 Hook 共享 15/10/5 秒外层、运行时和网络预算。
- source_evidence 只为名称候选核对本地 CMake/Git 来源及逐路径内容，不引入依赖审计。运行记录属于调用适配器，不改变 Gate 无状态性；SDK 原生取消信号桥接到执行线程，断连结束检查，不建立后台调度。详见 ADR-0018。
- Finish 调用内的 GitRunner（含嵌套依赖与 SSH 子孙进程）共享调用线程的临时取消信号和所属进程树控制；退出调用即清除，不存成任务状态。卫生遍历保留取消检查点，不把中断转换为来源阻塞后继续执行。
- scripts/build_delivery.py 在系统临时区构建 wheel 和插件，交付中使用同一选择器及 runtime-build.json 指纹；插件中的 stdlib 选择器是规范源码的逐字节副本，构建与契约测试拒绝漂移。只输出可审查包，不伪造安装、信任或旧 MCP 进程重载状态。

- `process_control.py` 是运行时和插件桥接共用的标准库进程控制实现，打包核对逐字节一致；`adapters/process.py` 只桥接 Finish 取消信号和诊断类型。GitRunner 的 Start/Hook/Finish 调用全部使用该实现，控制建立与启动计入预算；Windows Job 在实际命令前建立，超时/取消后确认所属进程退出。启动失败保留独立诊断，不伪装为测试失败。
- `application/command_syntax.py` 对明确命令做有界词法分析，区分文字数据/注释与调用词，支持字面量 shell 包装和 Windows 入口。它不运行命令、不解释 Python 等程序内部，也不代替宿主授权。
- Finish 记录格式 2 使用每次调用的 UUID 文件锁提供存活性证据，原子 JSON 保存进度和终态；查询仅只读探测和复读，兼容旧终态。锁不进入 Gate、Memory 或数据库，也不提供续跑和缓存许可。

- 清理扫描使用目录枚举缓存，不跟随链接，不为每个合成仓库派生 Git 进程。独立临时容器与项目内部产物采用不同的内容保护范围；两者均须由原生任务上下文确认归属，运行时仅报告路径检查结果。
- 交付摘要组合运行时指纹和插件内容，插件构建版本携带交付摘要，并保留逐文件摘要。原生安装源来自审查后的包，安装缓存由宿主管理；插件实际加载与信任通过宿主验证。
