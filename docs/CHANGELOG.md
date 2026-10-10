# 变更记录

### 宿主部署与清理闭环补修（2026-10-04，未发布）

- 精确区分独立临时容器和用户 checkout；允许容器内的合成仓库及内部链接，阻断外部关联、当前 checkout 祖先和无法检查的目标。扫描不跟随链接并保留 5 秒预算。
- 交付摘要覆盖运行时及插件文件；插件构建版本使用交付摘要，避免同名旧缓存混淆。安装、信任、Hook 执行和新 MCP 连接分别验收，不以源码测试代替宿主状态。

### 八类门禁缺陷补修（2026-10-04，未发布）

- MCP change_class 默认 null，Start 显式要求，Finish 根据正式代码变化要求；CLI 无类型 check 保持预检含义。
- 上游来源证明读取实际内容并比对固定 blob，抵御索引隐藏修改；目录枚举包含忽略项，input 例外仅限根目录。
- Hook 按调用词法识别操作与字面量包装；运行时、GitRunner 和插件桥接共用进程树控制，预算包含启动与控制建立。
- Finish 记录格式 2 增加 UUID 存活锁；释放锁后只读复查终态，旧 running 明确不可验证；写入失败保留系统诊断。
- Ruff 按 TOML 与官方优先级发现配置；运行目录用实际 Git 忽略/跟踪规则预检；Git 空白诊断区分检查发现与执行失败，保留原始输出。
- 无新增 MCP 工具、CLI 命令、数据库表或 DSH 兼容层；OpenCV 保持只读。桌面部署结果与隔离 MCP 验证分别记录。

### 门禁事实、运行入口与执行结果修复（2026-10-02）

- 共享 codex-os 入口和进程启动身份、配置预检与稳定诊断；.aios 不自动迁移，旧 MCP 不冒充新构建。
- Finish 增加 UUID、分项原子记录和只读查询；超时/取消/断连回收所属命令树，未完成结果无通过决策，核心 Gate 保持无状态。
- 名称只发现卫生候选，CMake 与固定上游来源逐路径核验；build/vendor 无整树豁免，真实源码副本仍阻断。推送远程按显式参数/Git 配置选择并真实检查所有推送及上游地址。
- 清理诊断保留请求与解析目标，宿主未提供具体规则时保持未知；技能、MCP schema、CLI 与可审查发布包增加契约回归。OpenCV 只读，发布/性能/业务限制保留为独立事实。

## [1.0.0] - 2026-09-10

### governance-core 轻量化大重构（ADR-0016，breaking change）

版本定稿：Dogfood 四案例全部通过（无 GitHub 阻塞/放行、正常后端流程、前端批准与豁免、子 Agent worktree 隔离与清理），当次基线环境验证通过，复杂度预算达标（MCP 7/8、CLI 7+1/8、Skills 8/9、活跃 docs 13/10~15、Gate 3、SQLite 5/6 表）。

## Unreleased

### 安全边界与数据保全修复（2026-09-24，ADR-0017）

- 清理按真实目标检查；统一项目/Worktree 解析和 Move to 保护，修正 branch -d/-D。薄 Hook 统一内核，错误明确拒绝；移除 ask 与脏文件归属推断，增加只读 --explain 和 15/10/5 秒超时预算。
- 前端批准/拒绝均更新事实并绑定内容摘要；旧批准需重新确认，补齐 css_fix 豁免。
- 数据库仅对已知指纹显式迁移，SQLite 一致性备份保留 WAL；未知/异常不重建。Memory 失败保留候选，写锁防止覆盖，检索刷新索引并统一 Secret 校验。
- Worktree 非强制 remove/branch -d，失败保留恢复记录；Finish 必需基线并覆盖完整任务差异；Secret Scan 检查实际暂存内容，缺失不再视为通过。
- 同步 8 个 Skill 契约，新增签名回归；保护已有 input，修正 Windows launcher/Docker 模板、Doctor 未知状态；修复类型契约而不放宽检查规则。
- 本轮为源码修改，不包含全局插件重装、Hook 信任、实际项目迁移或宿主审批设置变更。

### GitHub 官方 SSH endpoint 兼容（2026-09-17）

- 门禁与仓库检查共用精确主机解析；将 `ssh.github.com`（SSH URL 可指定 443）规范化为 `github.com`，不接受通配符、相似域名、任意 SSH 主机或非官方端口。
- 保留原始 `origin` 的 `git ls-remote` 可达性检查；补充官方端点、伪装域名、非法端口及不可达回归测试。


### governance-core 审计修复（fix/governance-hardening）

- fix(gates): Code Start 强制 GitHub remote + 复制式脏乱判定；开源调研文档要求 requirement_id 开头并给出 summary 与 Decision/reason，空模板/stale id/缺 reason 均阻塞，无布尔绕过。
- fix(frontend): 批准事实持久化为 docs/design/UI_SPEC.md 的 `approval:` 块（scope 精确匹配），删除调用方 approved 布尔绕过。
- fix(worktree): cleanup 先证明合并（`merge-base --is-ancestor <tip> <target>`），脏树与未合并一律拒绝；finish 置 ready（ready ≠ merged）；force 参数全面删除；Hook 只信任登记的真实 worktree，伪造 .worktrees/ 失败封闭，temp 判定跨平台。
- refactor(auth): 授权内核只判操作不判角色（principal/TRANSITION/policy_hash/RoleBoundary/GovernanceMode 删除）；output/ 可写，其纯净由卫生检查判定；OCI 话术改为"可能破坏项目持久数据"语义。
- fix(finish): 删除 --tests-passed/--docs-synced 自证；新增 core/checks.py 薄真实检查（声明的 --test-command、配置了 ruff 才跑 ruff、git diff --check、卫生、candidate 提醒非阻塞）。
- fix(memory): candidate 闭环——accept 校验并入/reject 丢弃/未知 id MEMORY_CANDIDATE_MISSING；CLI memory candidate 与 MCP 第 8 工具 memory_candidate。
- refactor(config): ProjectConfig 只保留运行时读取的字段（project_type 驱动模板，新增 code_paths），risk_level/环境/执行策略字段与 .codex/agents 角色档案删除。
- chore(repo): 删除 .codex-os/gates、environment.yaml、execution-policy.yaml、test-traceability.yaml；secret 扫描脚本晋升 scripts/secret_scan_incremental.py；.gitignore 合规检查（15 项运行时产物，等价写法允许）。
- docs: ADR 0001/0003/0009 随被删运行时退役；AGENTS.md/README/API_SPEC/GOVERNANCE_RULES/WORKTREE/MEMORY/DATABASE/TEST_PLAN/ARCHITECTURE 同步。

### governance-core 第二轮审计加固（fix/aios3-hardening）

- fix(gates): 开源调研事实增加 scope（行内/块列表）与 updated_at（YYYY-MM-DD）必填校验。
- feat(finish): Code Start 第二层复核——未提交/已暂存改动触及 code_paths 时必须携带 --change-class（研究类另需 --requirement-id）复跑 Code Start，间接写入（脚本生成源码）在完成时被拦截；无状态设计不变。
- fix(frontend): approval 块持久化 approved_by（decided_by 传递），SQLite 仍只是索引。
- docs(governance): AGENTS.md 收敛为十条宪法 + Git 节奏 + 原则化护栏，验证与实现边界细节下沉 GOVERNANCE_RULES；ADR-0010/0011/0015 标注部分 Superseded（只改状态行，不改历史正文）。
- chore(repo): 删除 .codex-os/tmp 一次性编辑脚本与本地缓存残留。
