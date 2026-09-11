# 治理规则

## 三 Gate（无状态评估器）

评估器位于 `src/codex_ai_os/core/gates.py`，同输入同输出，不持有隐藏状态。

- **Code Start**（`governance_check(stage="start")`）：GitHub remote 存在且可达（github.com）；"脏乱"精确判定——只有复制式目录/文件、被跟踪的污染内容、未解决冲突才阻塞，用户自己的未提交工作不算脏乱；开源调研分层适用，调研文档必须以 requirement_id 开头并给出 summary 与 Decision/reason（无布尔绕过）。无 GitHub 时允许读 input/、分析、调研、规划、文档；禁止正式 src/ 实现。
- **Frontend Approval**（`stage="frontend"`）：new_page / new_interaction_flow / major_ui_refactor 需要 PROTOTYPE.html 存在且 UI_SPEC.md 记录了匹配 scope 的 `approval:` 批准块；copy_change / component_bugfix 豁免。批准事实由 approval_record 写入文档，调用方布尔无法绕过。
- **Finish**（`stage="finish"`）：只跑可就地验证的薄检查（core/checks.py）——声明的 `--test-command`（给了才跑，失败即阻塞）、pyproject 配置了 ruff 才跑 ruff、`git diff --check`、仓库卫生、一次性文件清理、无目录副本版本、candidate 未处理提醒（非阻塞）。"测试通过/文档已同步"不再接受自证。

不可确定性观察的检查（如"需求范围已明确"）是 AGENTS.md 的过程纪律，不进运行时。

## 路径策略

- 受保护路径（治理通道内禁写）：`input/**`、`.git/**`、`.codex-os/state/**`、`**.env`、`**/credentials/**`。
- 治理规则路径（一律禁写）：`AGENTS.md`、`.codex-os/project.yaml`、`plugins/ai-engineering-os/**`。
- `input/` 是受保护的用户输入目录，不是禁止目录；扫描副本式脏乱时跳过 input/。
- `output/` 不是禁写目录：它是最终交付物目录，其纯净（无缓存/日志/副本）由 Finish 与仓库卫生检查判定。

## Hook 语义（保护用户资产，而非禁止专家工具）

- 无条件拦截：force push、删远端 ref、update-ref -d、compose down -v、volume rm/prune、对根/家目录递归强删。
- 上下文感知：`reset --hard`、`checkout --`、`clean -f`、`branch -D`、递归强删——主工作区拦，登记的真实 worktree 或系统临时目录放行；伪造的 `.worktrees/` 目录失败封闭。
- Memory 单写者：disposable worktree 内禁写 `docs/memory/`，只允许提交 candidate。
- pip/npm/pnpm/yarn/poetry/cargo/sed -i 等正常工程命令全面放行。
- 已初始化项目的 apply_patch / 重定向目标经 `codex-os authorize-hook` 内核裁决（仅主工作区）。

## 规则优先级

1. 用户当前明确要求 → 2. AGENTS.md / 硬治理规则 → 3. 已确认项目事实 → 4. 任务上下文 → 5. AIOS 建议。用户要求破坏事实或绕过安全规则时，指出冲突并请求确认，不静默执行。
