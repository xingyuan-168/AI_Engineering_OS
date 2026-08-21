# ERP 采购试点实施报告

日期：2026-08-21

实施分支：`codex/m5-erp-pilot`

实现提交：`d28f4bd4dec8f627770ca2b5c02d831df4d27fad`

## 结果

从空的临时 Git 仓库执行了完整 `new-project` 流程，最终状态为 `completed`。试点生成 FastAPI + SQLite ERP 采购 API，覆盖供应商、采购申请、提交审批、批准、采购订单和状态查询；不包含 UI 或生产发布。

运行时证据：8 个阶段任务、8 个独立本地 Commit、8 个 Worktree、8 个 Handoff、5 个人工门禁决定，审批绕过为 0。verify 前执行一次 `pause → resume`，恢复后 Task ID 保持不变。

## PA-001～PA-010

| 场景 | 结果 | 自动化证据 |
| --- | --- | --- |
| PA-001 项目初始化 | 通过 | 项目配置、文档、SQLite、初始 Git Commit |
| PA-002 目标解析和 G0 | 通过 | intake Task、G0 请求与审批事件 |
| PA-003 需求与范围 | 通过 | 产品需求、验收标准、G1 |
| PA-004 开源研究 | 通过 | FastAPI/HTTPX 官方来源、版本与 License |
| PA-005 架构设计 | 通过 | 架构、API、数据库、安全文档与 G2 |
| PA-006 Agent 与 Worktree | 通过 | 8 个一一关联的 Branch/Worktree/Task |
| PA-007 沙箱执行 | 受控通过 | Docker 缺失时 `SANDBOX_UNAVAILABLE`/exit 50；禁止宿主降级。真实容器启动待环境具备后复验 |
| PA-008 失败与恢复 | 通过 | verify 前 pause/resume，未复制 Task 或 Worktree |
| PA-009 G3 质量验证 | 通过 | fixture pytest、Review、安全报告、依赖审计与 G3 |
| PA-010 G4 发布候选物 | 通过 | manifest、CycloneDX SBOM、SHA256SUMS、回滚、审计、Memory 与 G4 |

## 质量门禁

```text
ruff: passed
pyright: 0 errors
pytest: 96 passed
coverage: 83.77%
pip-audit: no known vulnerabilities
git diff --check: passed
secret scan: passed
```

可复验入口：`uv run pytest tests/e2e/test_erp_workflow.py -q`。测试在临时目录创建和销毁 fixture；不会把 Runtime 数据库、日志、缓存或 Worktree 提交到 OS 主仓库。

## 环境遗留项

当前 Windows 主机未安装 Docker Desktop/WSL，因此不能声称真实容器启动已通过。Runtime 已按策略阻塞代码写入与高风险执行，不会自动回退到宿主机。安装 Docker Desktop/WSL2 并预拉取锁定 digest 后，应重新执行 PA-007 实容器复验。
