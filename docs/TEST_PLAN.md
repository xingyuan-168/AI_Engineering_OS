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
- 开源调研分层：豁免类误拦 = 失败；必查类漏拦 = 失败；调研文档缺 requirement_id/summary/Decision 元数据 = 失败；stale requirement_id = 失败；空模板 = 失败。
- 前端 Gate 分层：豁免路径直接放行；Gated 路径缺原型/UI_SPEC 均阻塞；批准来自 UI_SPEC 的 approval 块（scope 精确匹配），调用方布尔无法绕过。
- input/ 只读：治理通道写 input/ 被拒；副本扫描跳过 input/；output/ 可写但纯净由卫生检查判定。
- 危险命令：主工作区拦截 reset --hard/clean -f/branch -D/递归强删；登记的真实 worktree 与 TEMP 放行；伪造 .worktrees/ 失败封闭；pip/npm/sed -i/build 永不阻止。
- Finish 薄检查：声明的 --test-command 失败 = 阻塞；未声明不阻塞；TESTS_NOT_PASSED/DOCS_NOT_SYNCED 不再存在；MEMORY_CANDIDATES_PENDING 非阻塞。
- Memory：record 校验（Secret 拒绝、去重、类型/状态枚举）、reindex、单写者（worktree 内 docs/memory/ 写入被 Hook 拒、candidate 放行）、candidate accept 并入/reject 丢弃/未知 id 报 MEMORY_CANDIDATE_MISSING、损坏 JSONL 阻塞写入。
- Worktree：prepare 登记 / finish 拒绝脏树且置 ready / cleanup 未合并拒绝（merge 证明后通过）/ 注销后名称复用。
- 数据库：单迁移应用幂等、legacy 库导出重建、integrity 校验。
- repository：GitHub 缺失/不可达/主机不符、output/ 纯净、docs/archive 拒绝、.gitignore 覆盖 15 项运行时产物（等价写法允许）。

## 运行规则

不在"full pytest"之后重复跑 plugin/agent/MCP 子集凑证据；一个逻辑变更跑它映射的用例即可。
