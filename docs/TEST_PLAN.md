# 测试计划

## 默认验证（≤5）

1. 目标测试（narrowest mapped tests）。
2. `ruff check src plugins`。
3. `git diff --check`。
4. 仓库卫生 `codex-os check .`。
5. 必要时 pyright（schema/公共 API 变更）。

另加仓库 Secret Scan：detect-secrets 只扫本次修改（增量 `scan_file` 循环脚本，见 AGENTS.md Git 提交纪律）。

## 测试映射（Phase 5 重写后必须保留的正负案例）

- Gate A 脏乱精确判定：用户未提交工作放行；副本目录/文件拦截；api/v1/ 不误伤。
- 开源调研分层：豁免类误拦 = 失败；必查类漏拦 = 失败；`## Decision` 缺失 = 失败。
- 前端 Gate 分层：豁免路径直接放行；Gated 路径缺原型/UI_SPEC/批准均阻塞。
- input/ 只读：治理通道写 input/ 被拒；副本扫描跳过 input/。
- 危险命令：主工作区拦截 reset --hard/clean -f/branch -D/递归强删；.worktrees/ 与 TEMP 放行；pip/npm/sed -i/build 永不阻止。
- Memory：record 校验（Secret 拒绝、去重、类型/状态枚举）、reindex、单写者（worktree 内 docs/memory/ 写入被 Hook 拒、candidate 放行）、损坏 JSONL 阻塞写入。
- Worktree：prepare 登记 / finish 拒绝脏树 / cleanup 注销后名称复用。
- 数据库：单迁移应用幂等、legacy 库导出重建、integrity 校验。
- repository：GitHub 缺失/不可达/主机不符、output/ 纯净、docs/archive 拒绝。

## 运行规则

不在"full pytest"之后重复跑 plugin/agent/MCP 子集凑证据；一个逻辑变更跑它映射的用例即可。
