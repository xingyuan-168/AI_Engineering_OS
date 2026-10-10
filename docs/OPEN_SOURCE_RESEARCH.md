# Open Source Research

## Requirement

requirement_id: REQ-GATE-RELIABILITY
summary: Finish 调用的分项诊断、原子结果记录和取消后的所属进程树回收，保持无状态 Gate。
scope: finish-adapter, process-control
updated_at: 2026-10-02

## Candidates

### MCP Python SDK（已有依赖，MIT）
- URL: https://github.com/modelcontextprotocol/python-sdk
- 解决什么：原生工具参数、异步请求取消、进度通知和 stdio 会话。
- 可直接复用：是；使用已安装的 2.x 接口，不另建 MCP/Agent Runtime。
- 可二开：不需要；不修改 SDK。
- 值得学习：取消与连接生命周期；同步线程本身不会自动停止所属子进程。
- 风险：必须以真实 stdio 断连测试验证，不能仅模拟异常。

### Python subprocess / Windows Job Object
- URL: https://docs.python.org/3.12/library/subprocess.html
- URL: https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- License: Python PSF；Windows 系统 API，无新增包。
- 解决什么：外部声明测试执行及其子孙进程控制。
- 可直接复用：标准库、系统 Job API；已有 atomic_text 用于结果落盘。
- 可二开：不需要；只封装 Finish 外部命令。
- 值得学习：先挂起、加入 Job 再恢复；超时不能只杀 shell。
- 风险：Job 创建/归属失败必须停止启动，不凭 PID 误杀其他进程。

## Decision

decision: use
reason: 复用已有 MCP SDK、标准库和系统 API；仅增加既有 Finish 适配层诊断记录及进程回收，不增加工具、SQLite 表、后台任务、验证缓存或 Agent 调度。核心 Gate 不读取历史运行结果作为许可。

## Earlier research

2026-10-04 补充核实（不新增依赖）：继续 use 标准库和 Git。Python 的 [msvcrt.locking](https://docs.python.org/3.12/library/msvcrt.html) 提供非阻塞文件锁；POSIX 使用 flock。锁放在调用适配层，只读查询探测存活，不写任务表。[Git hash-object](https://git-scm.com/docs/git-hash-object) 不带 -w 只计算对象标识；保留内建换行处理，在已配置外部 filter 时报告证据不足。[Ruff 官方配置规则](https://docs.astral.sh/ruff/configuration/) 用于三种配置形式和优先级。Windows 继续复用上述 Job Object API，抽出同源标准库控制器供插件桥接与 GitRunner 使用，不增加执行平台。

## Requirement

requirement_id: REQ-GC-1.0
summary: governance-core 轻量化重构（ADR-0016）：把过度工程化的治理运行时收敛为无状态三 Gate + 轻量 Memory/Worktree，删除自有执行平台。
scope:
  - gates
  - memory
  - worktree
updated_at: 2026-09-11

## Candidates

### LangGraph / CrewAI / MetaGPT / OpenHands
- URL: https://github.com/langchain-ai/langgraph 等
- License: MIT 系
- 解决什么：多 Agent 编排、状态图运行时。
- 可直接复用：否——本层只做治理，不做 Agent 运行时。
- 可二开：否——引入即违反架构边界（ADR-0016 §2.12）。
- 值得学习：状态机的显式建模思想；本项目刻意反其道用无状态评估器。
- 风险：依赖膨胀、把 AIOS 重新拉回 Agent 平台。

### detect-secrets
- URL: https://github.com/Yelp/detect-secrets
- License: Apache-2.0
- 解决什么：Secret 检测。
- 可直接复用：是——作为轻依赖，只扫本次修改/staged diff。
- 可二开：否。值得学习：插件式规则。风险：低。
- 备注：AGENTS.md 的仓库 Secret Scan 封装。

### SQLite FTS5（标准库/编译选项）
- 解决什么：Memory 全文检索。
- 决策相关：放弃——单项目记忆量小，LIKE 检索 + reindex 足够，避免 FTS 维护与构建门槛。

## Decision

decision: build
reason: 治理层必须确定、可审计且不引入第二运行时；全部核心能力（三 Gate、审批、SQLite、JSONL Memory、worktree 生命周期）自研约三千余行即可覆盖，任何 Agent 框架依赖都会重建已删除的平台层。detect-secrets 以 use 方式作为轻依赖直接复用。
