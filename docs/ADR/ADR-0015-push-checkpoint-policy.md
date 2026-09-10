# ADR-0015：Git Push Checkpoint 策略

<!-- codex-os-document: {"schema_version":"1.2","document_version":"0.3.0","status":"review-ready","owner":"architect","requirement_refs":["VERSION-001","REPO-001"]} -->

- 状态：Review-ready（AGENTS.md 修订文案经人工确认后置为 Accepted）
- 日期：2026-09-08
- 决策版本：0.3.0 / API 1.2 / SQLite 0008
- 来源：`docs/proposals/AI-OS_v0.3_Governance_Consolidation_改进方案.md` P1-04（经 0.3.0 源码核对修正：push 配置实际位于 `domain/config.py` 的 `GitPushPolicy` 与 `.codex-os/project.yaml`，不在 execution-policy）

## 1. 上下文

0.3.0 治理核对确认：

1. `AGENTS.md` 与 `docs/IMPLEMENTATION_PLAN.md` 要求"每个逻辑变更一个 Conventional Commit 并立即推送"；`GitPushPolicy` 仅有 `remote_required` 与 `fixture_local_only` 两个值；所有 `gates/*.yaml` 声明 `git.push_required: true`。
2. 推送核验链路已健全：`application/workflow.py` 读取配置，`adapters/git.py` 用 `ls-remote` 实核远端分支头，Gate 证据在缺推送时生成 finding。
3. 立即推送的代价：网络异常会阻塞 Agent 的任务推进；长任务产生大量远端中间提交；实验性工作污染远端。

## 2. 决策

1. **Commit 保持强制**：每个完整逻辑变更仍必须是一个 Conventional Commit（不变）。
2. **Push 按项目策略执行**：`GitPushPolicy` 扩展为三个运行值：
   - `remote_required`（现状默认）：每个逻辑变更验证后立即推送；任务完成证据要求 push_status=pushed。
   - `checkpoint`（新增，推荐值）：提交即本地完成；在检查点事件强制推送并核验——task_complete、handoff 交接、gate 转换（G0-G4 审批）、release_candidate 与 final_release。非检查点的任务级提交允许暂时只在本地。
   - `release_only`（新增）：仅在 release_candidate 与 final_release 事件推送。
3. **Gate 证据不变**：`gates/*.yaml` 的 `git.push_required: true` 语义保持——Gate 转换本身就是检查点，任何策略下 Gate 证据都必须有已推送的 source Commit。`checkpoint`/`release_only` 策略下，task_complete 级证据的 push finding 仅在事件属于该策略的检查点集合时产生。
4. **文档修订**：`AGENTS.md` Git 事务规则改为"推送遵循项目 `git_push_policy`"；`docs/IMPLEMENTATION_PLAN.md` Git 交付协议同步。
5. **单调性**：项目只能把策略从宽松改严格（release_only → checkpoint → remote_required），不得反向放宽；配置加载时校验。

## 3. 被否决的选项

1. **把默认改为 checkpoint**：改变存量项目行为；AI-OS 自身仓库保持 remote_required，新项目可在 init 时选择 checkpoint。
2. **去掉远端核验**：`ls-remote` 实核是证据可信的根基，保留。
3. **按任务可覆盖 push 策略**：任务级覆盖即第二事实源，策略只随项目配置。

## 4. 后果

- 网络异常不再阻塞非检查点的工作推进；远端提交数量显著下降。
- 本地领先远端的窗口内，Git 证据会记录 push_status 并在检查点强制收敛；任何本地与远端分叉仍按"异常先报告"处理。
- `docs/TEST_PLAN.md` 与 traceability 的 Git 证据条目补充策略语义说明。

## 5. 验证

- `tests/unit/test_push_policy.py`：策略枚举、单调校验、检查点事件映射。
- 既有 e2e（remote_required）全部保持通过；新增 checkpoint 策略的 e2e 断言 task_complete 后本地提交允许未推送、gate 审批前强制推送。
