# 治理规则

## 三 Gate（无状态评估器）

评估器位于 `src/codex_ai_os/core/gates.py`，同输入同输出，不持有隐藏状态。

- **Code Start**（`governance_check(stage="start")`）：GitHub remote 存在且可达（精确识别 github.com 与 GitHub 官方 SSH endpoint ssh.github.com，后者支持 ssh:// 的 443 端口；不接受相似域名或通配符，对任务选定的全部推送 URL 及已配置 upstream 获取 URL 去重后真实 ls-remote）；"脏乱"精确判定——名称仅发现候选，CMake 缓存/精确探测路径/Git 状态或固定上游内容证据决定例外；不按 build/vendor 整树豁免。真实副本、被跟踪的污染内容、交付目录违规、未解决冲突阻塞，用户自己的未提交工作不算脏乱；开源调研分层适用，调研文档必须以 requirement_id 开头并给出 summary、scope（行内列表或块列表，至少一项）、updated_at（YYYY-MM-DD）与 Decision/reason（无布尔绕过）。无 GitHub 时允许读 input/、分析、调研、规划、文档；禁止正式 src/ 实现。
- **Frontend Approval**（`stage="frontend"`）：new_page / new_interaction_flow / major_ui_refactor 需要原型、UI_SPEC 和匹配 scope、原型摘要、规格正文摘要的批准块。copy_change / css_fix / component_bugfix 豁免。批准和拒绝均写入文档；拒绝撤销原批准，设计变更或旧块无摘要时要求用户重新确认，不自动补签。
- **Finish**（`stage="finish"`）：必须提供任务开始时保留在 Codex 原生上下文中的 `base_ref`；缺失返回 FINISH_BASE_REQUIRED，不回退当前 HEAD。初始空仓库明确使用 EMPTY_TREE。基线以来的已提交、已暂存、未暂存、未跟踪改动全部参与 code_paths 判断（含中文与重命名的 NUL 路径解析）。正式代码变更要求 change_class，研究类另需 requirement_id，并按配置中的 GitHub 主机复核 Code Start。空白检查覆盖 committed/staged/unstaged。声明的 test_command 失败阻塞，未声明显示 TEST_COMMAND_SKIPPED；已配置但缺失的 Ruff 阻塞，不能把未运行称为通过。另检查卫生、一次性文件和未处理 candidate 提醒。文档一致性由 Codex 原生 review，不接受 tests_passed/docs_synced 自证参数。

不可确定性观察的检查（如"需求范围已明确"）是 AGENTS.md 的过程纪律，不进运行时。

远程选择顺序：显式 remote → branch.pushRemote → remote.pushDefault → branch upstream remote → 唯一远程；仍有歧义则阻断，不固定回退 origin。上游为本地时验证引用，新分支无上游单独提示；可达不表示有推送权限。本地配置查询独立限时；一次请求的全部网络探测共享 5 秒预算，不重复探测。

Start/Finish/Memory 共用只读配置预检。支持既有 .codex-os 配置版本；.aios 单独报告产品家族不匹配，不初始化、不重建索引、不自动改目录。跨家族迁移必须另行映射规则与字段。

Finish 的 UUID 记录只供诊断：completed 且 allowed=true 才算通过；超时/取消/断连/不可用无最终 Gate 决策，记录已完成分项和 not_run 项，回收所属进程树，不后台续跑、不缓存许可。状态查询不重新检查仓库。调用契约见 API_SPEC.md。

## 默认验证

MCP Start 要求显式 change_class，默认 null；CLI 不带类型的 check 仅预检。Finish 正式代码缺少类型阻断为 CODE_START_UNVERIFIED，纯文档允许省略。上游内容证据直接比对文件与固定提交 blob，不依赖 assume-unchanged/skip-worktree 的差异结果；目录必须覆盖新增、忽略和修改内容。外部 filter 不自动执行，缺少可安全验证的内容证据则阻断。

Ruff 解析 TOML，优先采用根目录 .ruff.toml、ruff.toml，再检查 pyproject.toml 的 tool.ruff（含子表）。非法、不可读、缺少工具均阻断，仅无配置可跳过。Git 空白问题与命令失败分别报告，保存阶段、退出码和原文，不推测自由文本中的路径。

默认验证 ≤5 项：目标测试、ruff、`git diff --check`、仓库卫生（`codex-os check`）、必要时 pyright。每个逻辑变更还须通过仓库 Secret Scan：提交前运行 `python scripts/secret_scan_incremental.py --staged` 检查 Git 索引中的实际提交内容；显式文件缺失、无法读取或扫描失败均失败，不输出有效通过。

## 实现边界

- Python 3.12 + uv.lock 锁定依赖；Gate/审批/SQLite 全部自研自持，无第二模型客户端。
- 设计护栏默认保持轻量：MCP 工具/CLI 命令/活跃文档/Skills/Gate/SQLite 表数量保持现状，新增能力必须先证明必要性并经人工 review，失效能力及时删除；只做人工对照，不写运行时检测代码。
- 不做：strict assurance Profile、SBOM、镜像扫描、dependency audit、Verification Cache、Release 发布器、Host Operation lease、每命令 Evidence、自有 Agent/Tool Runtime、DAG 调度、自研 Secret 引擎、复杂审批系统、复杂 Research 系统、复杂 Memory 状态机。

## 路径策略

- 受保护路径（治理通道内禁写）：`input/**`、`.git/**`、`.codex-os/state/**`、`**.env`、`**/credentials/**`。
- 治理规则路径（一律禁写）：`AGENTS.md`、`.codex-os/project.yaml`、`plugins/ai-engineering-os/**`。
- 当前 checkout 根目录的 `input/` 是受保护的用户输入目录，扫描副本式脏乱时仅跳过该根目录；vendor/input 等嵌套同名目录仍检查。
- `output/` 不是禁写目录：它是最终交付物目录，其纯净（无缓存/日志/副本）由 Finish 与仓库卫生检查判定。

## Hook 语义（保护用户资产，而非禁止专家工具）

- 无条件拦截：force push、删远端 ref、update-ref -d、compose down -v、volume rm/prune、对根/家目录递归强删。
- 局部 Git 操作：`reset --hard`、`checkout --`、`clean -f`、`branch -D` 仅在登记且与 Git 实际清单一致的 disposable checkout 中具备局部放行条件；`branch -d` 不再被当作 `-D`。切换 cwd 或指定 Git -C 不会继承原位置权限；无法可靠解析的上下文拒绝并说明原因。
- 文件清理：Codex 根据任务上下文确认归属和用户授权；AIOS 检查精确、可解析目标。仅系统临时目录或 checkout 的 build/dist/.codex-os/tmp 下具体叶目标可进入自动检查，整个这些根、盘符根、家目录、仓库根、input/.git/output、跟踪文件、链接/junction 和含受保护子项均拒绝；“未跟踪”不构成删除授权。不建立任务凭证系统。诊断分别保留请求动作、原始/解析路径、AIOS 规则和原因；无法解析目标标为未知。宿主仅返回 blocked by policy 时具体规则未提供，不归因于 AIOS。复杂表达式或变量目标返回缺少的证据，不因 cwd 位于临时区就放行。
- Memory 单写者：disposable worktree 内禁写 `docs/memory/`，只允许提交 candidate。
- pip/npm/pnpm/yarn/poetry/cargo/sed -i 等正常工程命令全面放行。
- CLI、MCP、SessionStart、PreToolUse 共用根解析。逐目标确定治理项目，子目录、登记 Worktree、绝对/中文路径及 apply_patch 的 Update File + Move to 源/目标均受保护。明确但无法解析的 Shell 写入拒绝；间接生成的结果由 Finish 完整差异复核。
- 正常 Hook 只输出空对象或官方 deny/context 协议，不输出不支持的 ask，不把所有脏文件认作用户修改。Runtime 缺失/超时/异常/无效 JSON 时，明确写入或破坏操作返回带规则编号的拒绝，非敏感操作只提示未完成检查。外层 15 秒、Runtime 10 秒、一次请求网络预算最多 5 秒；没有第二套完整离线规则。
- `authorize-hook --explain` 为只读诊断：AIOS 规则编号、目标、原因、建议；绝不执行传入命令，也不代表宿主授权。宿主可独立拒绝执行或禁用 Hook。遇到宿主拒绝不得改 cwd、Shell、语言绕过；缺少具体宿主规则时如实说明未知。

## 规则优先级

独立系统临时容器的清理例外：目标不能是 checkout、位于用户 checkout 内或包含当前 checkout。其合成仓库中的 input/output、Git 跟踪夹具不按名称认定为用户资产；仍由 Codex 确认任务归属。内部链接仅在解析后留在容器内时通过，扫描不跟随链接；外部 Git 关联、未知 reparse 和不可读内容阻断。项目 build/dist 原有保护保持不变。检查共享 5 秒预算，超时为 CLEANUP_INSPECTION_TIMEOUT，不能称为通过。

Hook 对显式调用做有界词法分析，引用字符串、注释和 here-string 作为数据；Windows git.exe、字面量绝对入口及支持的 shell 包装保持参数边界。无法确定的显式上下文返回稳定诊断，不执行解释载荷。运行时与插件桥接共享进程控制，GitRunner 在 Start/Hook/Finish 中都拥有自己的子孙进程，启动和控制时间计入原预算。

Finish 运行文件和锁在创建前须经 Git 确认未跟踪且实际忽略；不自动改 .gitignore。UUID 文件锁仅证明一次调用存活，查询只读探测并复读终态；不以 MCP 进程存在当作仍在运行，也不把历史查询成功当作 Gate 通过。

1. 用户当前明确要求 → 2. AGENTS.md / 硬治理规则 → 3. 已确认项目事实 → 4. 任务上下文 → 5. AIOS 建议。用户要求破坏事实或绕过安全规则时，指出冲突并请求确认，不静默执行。
