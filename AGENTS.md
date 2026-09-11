# AI Engineering OS 宪法（AGENTS.md）

本仓库是 **Codex 的工程治理层**：无状态三 Gate（Code Start / Frontend Approval / Finish）只回答"允许/不允许及为什么"，不指导 Codex 怎么做专业工作。以下十条为硬治理规则，违反任何一条都不得继续。

## 十条宪法

1. **不改变 Codex 原生工程方式**：AIOS 只治理"能否做、何时做、做完留什么"；理解仓库、编码、调试、构建、测试、子 Agent 调度都是 Codex 的原生能力，AIOS 不重新实现。
2. **正式编码前必须通过 Code Start Gate**：GitHub remote 存在可达；开源调研分层完成并记录 Decision；仓库无复制式脏乱、无未解决冲突。无 GitHub 时允许读 input/、分析、调研、规划、写文档，禁止正式 src/ 实现。客观边界由 Hook/Runtime 自动强制，`codex-os check` 可主动预看阻塞原因。
3. **input/ 只读**：不得修改、重命名、删除 input/ 内容；output/ 只放最终交付物，不放缓存、日志、源码副本。
4. **禁止复制式版本管理**：不创建 src_v2/、backup/、copy/、final/、old/ 等副本目录或 *_final.py、*_v1.* 等副本文件；Git 历史是唯一归档，被否决的设计放 archive 分支。
5. **受影响文档必须同步**：只改受本次变更影响的文档；一行后端修复不得强制重写十个文档；PROJECT_CONTEXT.md 是派生缓存，永不作为事实源。
6. **前端先批准**：新页面/新交互流/重大 UI 重构必须先有 docs/design/PROTOTYPE.html + docs/design/UI_SPEC.md 并获用户明确批准；改文案、修 CSS、修组件 Bug 豁免。
7. **并行任务用 Worktree 隔离**：Codex 原生子 Agent 各自进入 .worktrees/ 下的登记 Worktree，不写主工作区与其他 Worktree；完成后 review、merge、及时 cleanup，不留孤儿 Worktree。
8. **一次性文件即用即删**：临时脚本、缓存、调试产物任务结束删除，不进 Git；确有复用价值才晋升到 scripts/ 或 tools/。
9. **有价值的才进 Memory**：只记 decision/bug/lesson/pattern（可选 project-summary）；单写者规则——子 Agent 只提交 candidate，主会话在 Finish 时统一写入 docs/memory/memory.jsonl；拒绝 Secret 与聊天内容。
10. **保护用户资产**：主工作区禁止 force push、删远端 ref、update-ref -d、递归强删用户文件；破坏性但局部的操作（reset --hard、clean -f、branch -D）仅在自己的 disposable Worktree 或系统临时目录内允许。

## Git 节奏

完整逻辑任务一个 Conventional Commit（docs:/chore:/feat:/fix:/refactor:/test: + 可选 scope）；handoff 或合并前必须 commit；里程碑 push。禁止 force push 与改写已推送历史；push 失败保持本地提交并如实报告，重试前核对远端 ref 与祖先关系。

## 验证

默认验证 ≤5 项：目标测试、ruff、git diff --check、仓库卫生（codex-os check）、必要时 pyright。每个逻辑变更还须通过仓库 Secret Scan（detect-secrets 只扫本次修改）。

## 实现边界

- Python 3.12 + uv.lock 锁定依赖；Gate/审批/SQLite 全部自研自持，无第二模型客户端。
- MCP ≤8 工具、CLI ≤8 用户命令、活跃 docs 10~15、Skills ≤9、Gate 3 类、SQLite ≤6 表——这是设计护栏，只做人工 review 对照，不写运行时检测代码。
- 不做：strict assurance Profile、SBOM、镜像扫描、dependency audit、Verification Cache、Release 发布器、Host Operation lease、每命令 Evidence、自有 Agent/Tool Runtime、DAG 调度、自研 Secret 引擎。

## 必读顺序

1. 本文件。
2. docs/README.md —— 事实文档索引，按任务需要加载对应文档，不再有长链必读。
3. 任务相关契约：docs/GOVERNANCE_RULES.md、docs/WORKTREE.md、docs/FRONTEND_GATE.md、docs/MEMORY.md。
4. 已接受 ADR（docs/ADR/）。
