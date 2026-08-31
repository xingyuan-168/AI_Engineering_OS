# AI Engineering OS 0.2.0 安全设计

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.2.0","status":"approved","owner":"security-reviewer","requirement_refs":["REQ-1.6.2","REPO-001","GATE-001","HANDOFF-001","WORKTREE-001","RELEASE-001","EXEC-001","HYGIENE-001","MEMORY-001"]} -->

安全目标是让不受信的仓库内容、Agent 输出、远端响应、依赖和日志无法绕过路径、执行、证据、审批与发布边界。所有高风险失败默认阻塞，不降级到宿主执行或人工口头确认。

## 1. 信任边界

| 区域 | 信任级别 | 规则 |
| --- | --- | --- |
| 已安装 Runtime 与校验后的迁移 | 受信代码基 | 仍受版本/hash 和测试约束 |
| `.codex-os` 严格配置 | 条件受信 | 未知字段、非法版本或安全放宽拒绝加载 |
| Codex Host / Plugin | 编排方，非安全事实源 | 只能调用公共 CLI/MCP；不能绕过 Runtime 写状态或高风险执行 |
| 项目 Git 内容与 Agent 输出 | 不受信 | 所有路径、文档、Handoff、Commit、hash 和命令重新验证 |
| GitHub/registry/PyPI 响应 | 不受信外部输入 | TLS、host allowlist、响应 Schema、Commit/digest/hash 绑定 |
| Docker/Podman daemon | 高权限边界 | 不挂 socket；Runtime 只调用受限 CLI argv；容器最小权限 |
| SQLite 与受管制品区 | 审计资产 | 文件权限、原子写、备份 checksum、Secret 脱敏和保留策略 |

## 2. 威胁与控制

| ID | 威胁 | 强制控制 | 失败结果 |
| --- | --- | --- | --- |
| `THR-001` | 非 GitHub/不可追溯仓库产生写任务 | 正式仓库预检绑定 HEAD/config/target；测试例外能力隔离 | `GITHUB_REMOTE_REQUIRED` 等 blocker |
| `THR-002` | 路径遍历、symlink/junction 或挂载逃逸 | realpath、祖先检查、逐段 reparse point 检查、Worktree 注册表和受管 mount kind | `PATH_ESCAPE`，不执行/不清理 |
| `THR-003` | 恶意/破坏性命令 | argv allowlist、风险分级、审批、锁定 OCI、无 shell 拼接 | `COMMAND_DENIED`/`APPROVAL_REQUIRED` |
| `THR-004` | 容器访问网络、Host、凭据或 daemon | network none、只读根、非 root、cap-drop、no-new-privileges、无 socket/凭据挂载 | execution failed/blocked |
| `THR-005` | 伪造测试/Review/Handoff | execution/review 数据库反查、source Commit/hash、Reviewer 分离、bundle hash | `EVIDENCE_INCOMPLETE`/`REVIEW_STALE` |
| `THR-006` | 并行写冲突或恶意覆盖 | 规范路径冲突检查、任务 Worktree、并发上限 4、集成锁、`--no-ff`、禁止 force/rebase | 串行化或 `MERGE_CONFLICT` |
| `THR-007` | 自动清理删除用户文件 | 仅 Runtime-created+registered disposable；清理预检与人工批准 | 保留现场，`WORKTREE_NOT_CLEANABLE` |
| `THR-008` | 依赖/镜像供应链风险 | `uv.lock`、完整 digest、PyPI provenance、pip-audit、SBOM、镜像扫描与阈值 | `DEPENDENCY_UNVERIFIED` |
| `THR-009` | Secret 进入 Git、日志、Memory、FTS | pre-write/commit/release Secret scan、日志脱敏、FTS 前扫描、项目隔离 | `SECRET_DETECTED`，撤销候选 |
| `THR-010` | 错误 PR/Commit/tag 发布 | G4 实时 GitHub 验证、Manifest/hash、独立授权、annotated tag | `GITHUB_PR_INVALID`/blocked |
| `THR-011` | 迁移损坏或旧 Gate 绕过 | 备份/checksum/integrity/FK/幂等/恢复、旧 run revalidation | `MIGRATION_FAILED`/只读恢复 |
| `THR-012` | 配置/Profile 降低风险 | extra-forbid、版本校验、安全字段单向收紧、用户覆盖只升不降 | `CONFIG_INVALID` |

## 3. 仓库与 GitHub 控制

1. 解析 remote 时只接受 `https://<allowed-host>/<owner>/<repo>[.git]`、`ssh://git@<allowed-host>/...` 或 `git@<allowed-host>:...`；拒绝 userinfo token、file、本地路径、非 allowlist host 和歧义 URL。
2. `git rev-parse --show-toplevel` 必须等于规范项目根；所有 Git 命令使用固定 argv 和 `-C <validated-root>`。
3. `git status --porcelain=v2` 检查脏状态，`git diff --name-only --diff-filter=U` 检查冲突。
4. reachability 使用 `git ls-remote`，HEAD pushed 使用本地 upstream ref 与 remote ref Commit/ancestry 对比；不得以 remote 名称存在代替可访问性。
5. 权限 probe 优先使用 GitHub API 查询 authenticated user/repository permissions；不得用空 Commit 或试探 push 修改远端。
6. remote URL/错误日志在持久化前移除 userinfo、query、header 和 token；只保存 host/repo hash。
7. 正式 `workflow_start` 在最新 passing audit 失效时阻塞，不复用其他 HEAD 或项目的报告。

## 4. 路径与仓库卫生

- 路径验证顺序：拒绝绝对输出/空/`.`/`..` -> 与 Worktree 根拼接 -> `resolve(strict=False)` -> 确认 relative_to -> 逐段检查 symlink/junction/reparse point -> 对已存在文件重新 `resolve(strict=True)`。
- Windows 比较使用大小写不敏感的规范路径，拒绝 device path、ADS、UNC（除非项目根本身是显式批准 UNC）、保留设备名和末尾点/空格歧义。
- Windows 绝对项目根与 Worktree 路径在持久化前统一解析并转为正斜杠 UTF-8 文本；包含 `U+FFFD` 的新记录 fail closed。Plugin MCP launcher 固定 `PYTHONUTF8=1` 与 `PYTHONIOENCODING=utf-8`；Doctor 只报告损坏表/记录/字段，不推测恢复中文路径。
- 禁止目录/文件规则只扫描项目自有树；排除 `.git`、`.venv`、依赖、受管 `.worktrees` 和 `.codex-os/state|logs|cache|tmp|artifacts`。
- Git 跟踪扫描使用 `git ls-files -z`，不能依赖文件系统遍历推断 tracked 状态。
- `.gitignore` 校验以必需类别而非固定单一模板判断，避免合法更严格规则误报。
- 自动清理不接受 glob、未解析环境变量或用户传入任意绝对路径；数据库 registry ID 是唯一清理入口。

## 5. ExecutionService 安全格

### 5.1 镜像

0.2.0 目标镜像：

```text
python:3.12.14-bookworm@sha256:852282e520cc1754221fb2e061ab35b13b596e8112a731d60e2a8b471c973b7a
```

该引用是官方完整 Bookworm 构建输入。执行前 Adapter 必须保存 registry index digest 与实际平台 digest，并用 `image inspect` 验证 architecture；`--pull=never` 防止标签漂移。镜像必须生成稳定标识的 SBOM 和离线 Trivy 报告；critical/high finding 没有有期、命名批准例外时 G3 失败。

### 5.2 固定 OCI 参数

- `--rm`、唯一且校验后的容器名。
- `--network none`、`--read-only`、`--cap-drop ALL`、`--security-opt no-new-privileges:true`。
- `--user 65532:65532`、`--pids-limit 256`、CPU/内存/超时限制。
- 工作目录为 `/workspace`，只读或读写属性由任务类型固定；根文件系统无写权限，临时目录使用受限 tmpfs。
- 只挂载当前 Worktree 与受管 artifact/cache；禁止项目根、用户目录、`.git` common dir、SSH、云凭据和 Docker/Podman socket。
- 命令为 argv 数组，不经 `shell=True`、`cmd /c`、PowerShell 字符串或容器 shell 二次解释。

### 5.3 生命周期

ExecutionService 检查 task lease、Commit 和 clean baseline，写 execution intent 后启动容器。stdout/stderr 流式脱敏并限制大小；超时先正常 stop 再 kill，均记录。结束时保存 exit code、镜像/命令/report hash，检查 Worktree dirty；非预期写入使证据无效。

`doctor` 未发现可用 Docker/Podman daemon 或 digest 时，只读需求/文档/状态工作可继续；实现、测试、删除、迁移和发布返回 `SANDBOX_UNAVAILABLE`/`SANDBOX_IMAGE_UNAVAILABLE`。

## 6. 多 Agent、Review 与合并

- 每个写任务必须声明精确 `allowed_paths`；目录授权不隐含父目录或相邻文件。
- 调度前比较规范路径、祖先关系和 glob 交集。无法确定时串行，不以 Agent 承诺替代检查。
- Reviewer/Security Reviewer 的任务契约不含被审源码写路径；Review 只通过结构化接口写 SQLite/报告。
- Handoff `ready` 不是信任结论；accepted 必须绑定 producer 之外的 Reviewer、被审 Commit、报告 hash 和无开放 high/critical finding。
- 合并前重新验证 task branch remote Commit、Handoff source Commit、integration head、Worktree clean 和锁 token。
- 合并冲突执行 abort 并保留任务 Worktree；不自动 checkout/ours/theirs、force push 或 rebase。
- 依赖任务只在 producer accepted 且 merged 后解锁；join barrier 在单一数据库事务内重算。

## 7. Gate 与证据安全

- Artifact path/hash/source Commit、Check execution/report、Review Commit/report 全部由 Runtime 反查。
- Gate bundle 采用规范 JSON hash 并绑定 run/gate/state_version/version；任一证据变化使 bundle/approval stale。
- 伪造字符串、宿主命令输出、skip 的真实 OCI 测试、错误 Commit 报告或缺少 execution ID 均不能通过 G3。
- G3 检查必须在同一 integration HEAD 上运行；任何后续 merge 使 G3 失效并重新验证。
- Gate reviewer 不能与产生关键证据的 Agent 相同；G4 还要求独立 release authority。

## 8. 依赖、Plugin 与 Hook

- 依赖只从 `uv.lock` 安装，使用 `uv lock --check`；变更更新 License、SBOM、审计和 ADR（重大变化）。
- `pip-audit` 报告与来源 Commit 绑定；无法查询 advisory 源时状态是 unavailable，不是假定通过。
- Plugin validator 校验 manifest、MCP Schema、Skill frontmatter、Agent Profile 和 Hook fixture；Plugin 版本与核心 0.2.0/Plugin API 1.2 一致。
- 依赖与扫描缓存只能由经网络审批的 verification prepare 生成，分别绑定 `uv.lock` hash、Linux OCI 平台、Python 版本、执行镜像、时间和来源；正式 Gate 只离线消费逐文件 hash 校验且无 symlink/junction 的只读 wheelhouse、pip-audit snapshot 和非空 Trivy DB snapshot。
- `.codex/` Hook 必须由人复核信任；Hook 只能调用受限入口，不携带 Secret，不把内部 Workflow 事件冒充 Host 生命周期事件。
- Plugin `PreToolUse` Hook 是防御纵深和即时提示，不是权限、路径或命令安全边界。它拦截直接出现的 force push、Git ref 删除及 Windows/Unix 宽范围递归删除，但不承诺解释变量拼接、别名、splatting 或间接脚本；最终控制必须由 Runtime 的结构化 argv allowlist、风险分级、路径校验、审批和 OCI 隔离执行。项目 `.codex/hooks.json` 有意不复制插件规则；插件未启用或 Hook 未经信任时，Runtime 仍必须 fail closed。
- 项目 `.agents/skills/` 不得创建 Plugin 同名 override；Runtime 拒绝歧义 Skill resolution。

## 9. Memory 与 Secret

候选 Memory 先解析 content/source real path 与 hash，然后执行 Secret/PII 检查、项目 scope 和 confidence 验证。只有通过的派生文本进入 `memory_search_documents`/FTS5。

Memory 默认按 `project_id` 查询；跨项目复用必须创建带来源、scope、批准人的 link。来源 hash 改变时 active 记录立即进入 needs_review 并从默认搜索结果隐藏。

日志/Memory 脱敏至少覆盖 GitHub/云/API token、Authorization/Cookie、私钥、密码/connection string、高熵候选和用户配置的敏感键。脱敏前原文不写临时文件；检测报告本身也不能回显 Secret。

## 10. Release 安全

1. Release task 只在 G3 approved 后创建，并使用 release Worktree。
2. 可提交发布文件必须位于该 Worktree；二进制制品只位于受管 artifacts 目录并被 Git 忽略。
3. Manifest、SBOM、checksums、rollback、CHANGELOG、ADR 和 Memory 从 Commit 或受管审计区重读；candidate manifest 区分 integration source Commit 与 candidate Commit，final manifest 记录远端发布对账。
4. G4 先持久化授权和 publish operation，再使用 GitHub API 验证 PR number/URL、head/base、approved/merged 状态和 merge Commit；用 Git 验证目标分支包含 PR merge Commit。
5. release authority scope 必须精确为 `tag-and-github-release`；不接受空 reason、过期 state version 或预先批准其他 Commit。
6. annotated tag message 包含 version、run ID、Manifest hash、merge Commit；现有不同目标同名 tag 返回冲突，不覆盖。
7. 创建或复用 draft Release，上传后逐项核对资产 hash，再发布。结果未知或部分失败进入 reconcile_required；按相同 operation/Manifest 幂等对账，不得删除 tag 或重写历史。
8. 本系统不保存生产部署凭据，也不提供部署入口。

## 11. 迁移与恢复安全

- 迁移前锁写、checkpoint WAL、SQLite backup API、生成/验证 SHA-256，并从备份运行 integrity/FK 检查。
- 迁移文件 checksum 与 `schema_migrations` 不同立即阻塞；不得执行修改后的旧迁移。
- 0004-0007 各自原子；未知 Memory 状态、FTS5 不可用或 FK 失败触发“临时库恢复校验 -> 原子替换”，不得在未校验备份上覆盖活动库。
- 旧活动 Workflow 标记 revalidation required，旧 Gate/自由文本验证不能直接发布。
- 备份、失败库和迁移日志属于 audit-evidence，不由任务清理删除。

## 12. 网络与凭据

默认网络拒绝。允许的宿主只读访问包括项目配置中的 GitHub/GHE、PyPI advisory、Docker registry 和经批准的官方研究来源；依赖安装、push、PR/tag/Release 属于独立操作并按风险/授权控制。

凭据只通过宿主安全存储或进程级短期注入传给特定 Adapter；不写 `.codex-os`、SQLite、日志、命令 hash 原文或容器挂载。Runtime 不记录 bearer token，并清除子进程不需要的环境变量。

## 13. 安全事件

发现 Secret、路径逃逸、未授权写入、恶意依赖、证据伪造或错误发布时：

1. 原子地将相关 task/run/release/memory 标记 blocked/needs_review。
2. 停止仍在运行的受影响 execution，保留脱敏现场与 hash。
3. 撤销/轮换凭据，禁用相关发布授权；不自动删除 Git 历史。
4. 创建安全事件、影响范围、来源 Commit、恢复建议和命名负责人。
5. 修复通过新的 Commit、Review、Gate bundle；发布历史用 revert/新版本修正，不 force rewrite。

## 14. 必测安全场景

- remote URL userinfo/token、非 GitHub host、DNS/SSH/HTTPS 不可达、权限不足和错误 upstream。
- `..`、绝对路径、UNC/device/ADS、symlink/junction、case/末尾点歧义、Worktree/common Git dir 逃逸。
- shell 元字符、未允许 argv、超时、资源超限、网络、root、rootfs 写、capability 和 socket 挂载。
- 三任务并行、路径重叠串行、Review producer 自审、stale Commit、冲突 abort、锁过期恢复和清理未知文件。
- 自报 passed、伪造 execution/report/hash、G3 后 Commit 漂移、旧 Gate/旧配置升级。
- Secret 进入文档/日志/Memory/FTS、跨项目搜索、source hash 变化。
- PR head/base/merge 不匹配、同名 tag 不同 Commit、GitHub Release 失败和部署权限隔离。
- managed Worktree/coordinator root 混淆、调用方自报 reviewer/approver、40/64 位 Commit、Trivy 数据过期、镜像 high/critical finding、无 `gh` 与 Podman machine 停止。

## 15. 剩余风险登记

| 风险 | 等级与前提 | 受影响边界 | 当前证据与缓解 | 剩余风险/关闭条件 |
| --- | --- | --- | --- | --- |
| `RISK-OCI-SUPPLY` | 中；registry 中锁定 digest 被发现新漏洞或签名链不可用 | ExecutionService/依赖供应链 | digest pin、SBOM、镜像扫描、默认断网、最小权限 | 中；G3 必须记录扫描时间和 findings，high/critical 未处置即阻塞；后续由新 ADR 评估签名验证 |
| `RISK-GITHUB-ADMIN` | 中；拥有管理员权限的人绕过分支保护或篡改外部审批 | GitHub/G4 | PR 实时校验、Git 可达性校验、principal/authority 审计、禁止 Runtime force push | 中；需要仓库管理员人工复核保护规则和 Hook 信任，G4 保存复核证据 |
| `RISK-WINDOWS-REPARSE` | 中；Windows junction/reparse/大小写别名在验证后被替换 | Host/Worktree 路径 | handle-based canonicalization、commonpath、reparse 拒绝、open-before-use、受管根目录 | 低到中；所有写/挂载路径的 TOCTOU 负向测试和真实 Windows fixture 通过后降为低 |
| `RISK-SELF-BOOTSTRAP` | 中；旧 Runtime 正在实现其自身更强 Gate，旧证据不足 | 0006 Runtime/0007 Runtime | migration revalidation、旧文本 evidence 不满足新 Gate、所有新规则先有契约和测试 | 中；未 mock 公共 MCP E2E、真实 Podman 与 0006->0007 恢复全部通过后关闭 |
| `RISK-RELEASE-EXTERNAL` | 中；tag 已推送而 GitHub Release API 暂时失败 | Git/GitHub Release | 幂等对账、同 Manifest 重试、不删除 tag、不重写历史、Workflow blocked | 低到中；GitHub Release 成功并将 release ID/hash 写入 G4 证据后关闭 |

Security Reviewer 必须在 G3 对每项记录前提、来源 Commit、验证报告、处置负责人和是否接受；不得用“低风险”替代证据。未关闭的 high/critical 风险阻塞 G3，未授权的中风险阻塞 G4。

## 16. 安全完成定义

所有 P0/P1 控制必须由负向测试证明不可绕过；安全 Reviewer 对 integration HEAD 提交无未解决 high/critical finding 的结构化报告；真实 Podman、Secret、依赖、镜像/SBOM、路径逃逸、GitHub G4 和恢复证据全部进入 G3/G4 bundle。
