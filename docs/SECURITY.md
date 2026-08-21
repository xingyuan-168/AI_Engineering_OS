# 安全基线

## 威胁

- Agent 执行破坏性命令或访问未授权路径。
- 提示词、日志或 Memory 泄露凭据和敏感数据。
- 外部依赖含有恶意代码、漏洞或不兼容 License。
- 多 Agent 合并未经测试的修改。
- 发布候选物缺少完整证据。
- Plugin、Agent 或项目配置绕过宿主和本地执行策略。
- Worktree、交接产物或路由决策被伪造、串用或跨项目泄露。

## 控制措施

1. 命令 allowlist、dry-run、超时、工作目录和允许路径限制。
2. 生产、删除、迁移、凭据访问和网络外传操作必须人工确认。
3. 日志和 Memory 默认脱敏，禁止写入 token、密码和私钥。
4. 依赖固定版本并执行 `pip-audit` 等安全扫描。
5. 合并前执行测试、静态检查、Secret 扫描和安全 Review。
6. 每个 Agent 使用独立 Worktree 和最小权限。
7. 开源 License 不明时阻止进入技术栈。
8. Plugin 只能通过 Local CLI Runtime 请求写文件、写 SQLite、执行命令、访问网络或读取凭据；Plugin Hook 失败不回滚核心状态。
9. Memory 按 `project_id` 和 `scope` 隔离，写入前执行 Secret/PII 脱敏；跨项目复用必须有来源、适用范围和人工批准。
10. Worktree 路径必须位于 `.worktrees/<workflow-id>/<agent-name>/<task-id>/`，通过绝对路径、符号链接和容器挂载检查防止越界。
11. Agent Handoff 的 artifact hash、来源 Commit、测试结果和任务 ID 必须由消费者重新校验；不匹配即阻塞。
12. Routing Decision 只能增加审批和安全限制，用户覆盖不能降低风险等级或放宽执行策略。

## 沙箱基线

- Docker/Podman 是代码、测试和高风险命令的默认执行环境。
- 网络默认关闭；宿主项目根目录、用户目录、凭据目录和 Docker socket 不挂载。
- 容器不可用时，代码写入、迁移、网络、删除和发布进入 `blocked`，不得静默宿主机执行。
- 命令按 L0-L4 分级；L3/L4 必须人工确认，L4 在 V1 不自动执行。
- 每个容器限制 CPU、内存、磁盘、进程数和时长，超限即终止并审计。

详细执行策略见 [EXECUTION_POLICY.md](EXECUTION_POLICY.md)，日志和审计字段见 [OBSERVABILITY.md](OBSERVABILITY.md)。

## 安全事件

发现凭据泄露、未授权写入、恶意依赖或生产误操作时，立即暂停 Workflow，保留事件证据，撤销暴露凭据，记录 ADR/事件报告并人工决定恢复方式。
