# ADR-0011：统一治理授权内核与三层强制模型

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.3.0","status":"accepted","owner":"architect","requirement_refs":["GOV-001","REPO-001","HYGIENE-001"]} -->

- 状态：Accepted
- 日期：2026-09-08
- 决策版本：0.3.0 / API 1.2 / SQLite 0008
- 来源：`docs/proposals/AI-OS_v0.3_Governance_Consolidation_改进方案.md` P0-01/P0-02/P1-08（经 0.3.0 源码核对修正）

## 1. 上下文

0.3.0 治理核对确认三类事实：

1. `application/governance_policy.py` 已实现完整的策略决策构件——`path_access`（受保护路径与审批路径的唯一决策函数）、`BASELINE_PROTECTED_PATHS`、`approval_gated_paths`、`BASELINE_ROLE_BOUNDARIES` 与路径词法校验——但它们在 `src` 运行时中零调用点，仅被单元测试引用。
2. 写入时刻不存在统一裁决点：MCP（`cli/mcp_server.py`）与 CLI（`cli/app.py`）直接调用 Service 层；前缀式 allowed-path 匹配在 `adapters/git.py`、`application/environment_operations.py`、`application/workflow.py`、`infrastructure/evidence.py` 各自内联实现且语义互有差异；Codex 宿主原生入口（apply_patch、Shell、Git CLI）的写时刻完全在 Runtime 校验之外，仅有 task_complete/Gate 证据的事后校验。
3. Plugin Hook（`plugins/ai-engineering-os/hooks/pre_tool_use.py`）是独立维护的 15 条正则黑名单，只读取 `tool_input.command`，不解析 apply_patch 内容，与 Runtime 零通信；`docs/SECURITY.md` 与 `docs/SCOPE.md` 将其定位为"防御纵深，不是安全边界"。

## 2. 决策

1. **唯一授权内核**：新增 `application/authorization.py`，提供唯一入口 `authorize(request) -> AuthorizationDecision`。`AuthorizationRequest` 携带 request_id、principal/role、source（mcp/cli/hook/internal）、workflow_id、task_id、tool、operation（read/write/execute/transition）、paths 与 command；`AuthorizationDecision` 返回 ALLOW/ASK/DENY、rule_id、reason 与 policy_hash。内部判断顺序：Run 有效性 → Task 存在 → Role 边界 → Operation 允许 → Path 在 allowed scope → Path 命中 protected scope → 是否需要 Approval → 环境限制 → Git/Release 特殊规则。
2. **激活既有构件**：内核内部复用并激活 `path_access`、`BASELINE_ROLE_BOUNDARIES`、路径词法校验与 `EffectiveGovernancePolicy`；不新建第二套路径规则。`adapters/git.py`、`environment_operations.py`、`workflow.py`、`evidence.py` 中的四处内联前缀匹配全部改为委托内核；`environment_operations` 的 argv 黑名单改为调用内核的命令判定。
3. **三层强制模型**（取代"Hook 仅防御纵深"的单一表述，写入 `SECURITY.md` §8 与 `SCOPE.md` §1）：
   - 第一层 Runtime 强制：MCP 与 CLI 的全部写类/状态变更入口在 Service 调用前经内核裁决，DENY 即拒绝；此层为可信任边界。
   - 第二层 Hook 强制：PreToolUse Hook 升级为内核的宿主侧客户端——解析 apply_patch 的全部目标路径（新增/修改/删除/重命名/多文件）与 Shell 常见写入模式（重定向、tee、cp/mv/rm、sed -i、git checkout/restore/reset/clean、包管理安装、docker volume、release 命令）后调用 `codex-os authorize-hook` 子命令取得裁决；Runtime 不可达或超时（5 秒）时降级为现有正则黑名单并记录事件。此层为尽力强制点，宿主可禁用，不构成可信任边界。
   - 第三层事后证据：task_complete 与 Gate 证据对已提交路径、推送状态与制品 hash 的 fail-closed 校验维持不变，作为前两层失效后的最终兜底。
4. **ASK 语义**：内核返回 ASK 时，MCP/CLI 拒绝本次操作并引导走既有 `approval` 流程；不新增审批通道。运行模式下对治理规则文件（`AGENTS.md`、`profiles/`、`gates/`、`plugins/ai-engineering-os/**`、`.codex-os/*.yaml`）的写入默认返回 ASK。
5. **不引入完整 Session 管理**：沿用 `InvocationContext` 并扩展 source/workflow_id/task_id 传递；Run + Task 即授权语境。

## 3. 被否决的选项

1. **把 Hook 作为唯一安全边界**：宿主可禁用 Hook，且 Hook 进程不持有 Runtime 状态；与三层模型冲突。
2. **为 apply_patch/Shell 构建完备语法解析器**：Shell 语义无穷（变量、别名、间接执行），完备解析不可达成；以常见模式覆盖 + 第一层与第三层兜底为诚实边界。
3. **新建独立 Session 服务**：0.3.0 无跨进程会话需求，Run/Task 已承载授权语境，新增服务违反收敛原则。
4. **在四处调用点各自修补而非收敛**：保留语义互异的第二事实源，正是本次整改要消除的漂移。

## 4. 后果

- 同一受限操作经 MCP、apply_patch（Hook fixture）、Shell、Git 入口得到一致裁决，Case A-E 测试矩阵可自动化。
- `path_access` 与 Role Boundary 首次在运行时生效；受保护路径与审批路径不再只是编译产物。
- Hook 的信任边界如实降级为"尽力强制点"，安全宣称与实现一致；内核为纯内存策略 + SQLite 只读查询，Hook 5 秒超时内可完成。
- 四处内联匹配删除后，路径匹配语义只有一处定义。

## 5. 验证

- `tests/unit/test_authorization.py`：决策矩阵（protected/allowed/role/operation/approval 组合）。
- `tests/integration/test_plugin_hooks.py` 扩展：apply_patch 多文件 patch 的路径解析与 DENY；Shell 重定向与 git 危险命令经 `authorize-hook` 的裁决一致性；Runtime 不可达时的降级行为。
- `tests/e2e/test_v11_governed_workflow.py` 保持通过：三层模型不改变既有受管流程结果。
- 全仓断言：`adapters/git.py` 等四处不再包含独立前缀匹配实现。
