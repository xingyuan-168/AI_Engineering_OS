# 测试计划

## 宿主闭环补充

`test_cleanup_closure.py` 覆盖独立临时容器中的合成仓库、内部链接、不跟随链接扫描、外部 Git 关联、当前 checkout 祖先保护、不可读与超时。项目 build/dist 的跟踪文件和受保护子项规则保持阻断。

交付验收必须核对原生安装、Hook 信任及实际执行、新 MCP 连接的构建身份、缺失的危险字面量回归，以及全部所属临时夹具清理。隔离 MCP 通过不能替代桌面宿主验收，旧失败记录不能被成功复测覆盖。

## 默认验证（≤5）

1. 目标测试（narrowest mapped tests）。
2. `ruff check src plugins`。
3. `git diff --check`。
4. 仓库卫生 `codex-os check .`。
5. 必要时 pyright（schema/公共 API 变更）。

另加仓库 Secret Scan：提交前 `python scripts/secret_scan_incremental.py --staged`，检查索引而非仅工作区；缺失/不可读/扫描错误失败。

## 测试映射（Phase 5 重写后必须保留的正负案例）

- Gate A 脏乱精确判定：用户未提交工作放行；副本目录/文件拦截；api/v1/ 不误伤。
- 开源调研分层：豁免类误拦 = 失败；必查类漏拦 = 失败；调研文档缺 requirement_id/summary/Decision 元数据 = 失败；stale requirement_id = 失败；空模板 = 失败。
- 前端 Gate 分层：豁免路径直接放行；Gated 路径缺原型/UI_SPEC 均阻塞；批准来自 UI_SPEC 的 approval 块（scope 精确匹配），调用方布尔无法绕过。
- input/ 只读：治理通道写 input/ 被拒；副本扫描跳过 input/；output/ 可写但纯净由卫生检查判定。
- 危险命令：main 指向安全临时叶目标可通过客观检查；temp 指向外部资产拒绝；宽泛根、跟踪文件、保护子项、链接、变量/复合表达式拒绝；branch -d 与 -D 区分；Git -C 不继承错误上下文。删除载荷只做诊断，真实删除仅发生于专门创建的 Worktree 夹具。
- Finish 薄检查：声明的 --test-command 失败 = 阻塞；未声明不阻塞；TESTS_NOT_PASSED/DOCS_NOT_SYNCED 不再存在；MEMORY_CANDIDATES_PENDING 非阻塞。
- Memory：record 校验（Secret 拒绝、去重、类型/状态枚举）、reindex、单写者（worktree 内 docs/memory/ 写入被 Hook 拒、candidate 放行）、candidate accept 并入/reject 丢弃/未知 id 报 MEMORY_CANDIDATE_MISSING、损坏 JSONL 阻塞写入。
- Worktree：prepare 登记 / finish 拒绝脏树且置 ready / cleanup 未合并拒绝（merge 证明后通过）/ 注销后名称复用。
- 数据库：普通读取不重建、已知指纹显式迁移、一致性备份包含 WAL 提交数据；未知结构/损坏/忙库/活动 Worktree/备份失败保留原库。
- repository：GitHub 缺失/不可达/主机不符、output/ 纯净、docs/archive 拒绝、.gitignore 覆盖 15 项运行时产物（等价写法允许）。

## 审计修复专项回归

- test_safety_boundaries：跨 cwd/子目录/中文绝对路径、Move to 进入 input、reparse、只读 explain；前端拒绝/摘要变化/旧批准/跨 scope；Finish 已提交代码、暂存空白、EMPTY_TREE。
- test_plugin_hooks：缺失/超时/异常/无效 JSON/不支持 ask 全部明确拒绝敏感操作，读取显示检查不可用；Runtime 响应透传契约。
- test_memory_store、test_safety_contracts：候选失败保留、完全一致的重试合入、并发无丢记录、索引刷新、tags Secret 同步校验、Worktree 仅候选；初始化保护已有 input，迁移不创建文档，MCP 拒绝在索引不可用时仍有效。
- test_plugin_skills：所有 Python MCP 示例绑定真实签名，调研 Markdown 示例满足真实 Gate 契约。
- Worktree 部分失败不强制，登记失败干净回收/脏对象保留；Windows launcher 退出码与 Docker context 契约；构建 wheel/sdist 后核对所含源码与迁移资源。

## 运行规则

2026-10-04 补修映射：test_gate_checks_followup 覆盖 MCP 缺省类型、CLI 同输入判定、Ruff 三配置与优先级及空白检查阶段；test_source_evidence_followup 覆盖隐藏索引修改/真实内容/换行/filter、嵌套 input 和忽略新增内容；test_execution_record_followup 覆盖实际忽略反转/跟踪记录/Git 错误、持续写入失败及只读锁查询（同进程/独立 CLI/并发/完成竞态/旧记录/链接边界）；test_command_control_followup 验证控制建立预算、真实启动失败及命令词边界。test_finish_execution 保留真实取消/断连/子孙进程与无关进程保护；test_plugin_skills/test_package 验证八工具 schema 和两份控制器的一致性。

本轮新增包含危险 Git 字面量的测试补丁被当前宿主 PreToolUse 拒绝，未落盘、未改用其他工具绕过；已存在的危险操作测试继续运行。该项宿主拒绝与测试执行失败分开记录，不能以现有测试通过替代新增用例的验收。

首次完整执行 `d1fbd201-46e2-4b73-9a9e-6bc77497c43a` 为 completed/allowed=false：325 通过、3 失败，完整输出保存在 checkout 的忽略记录目录。失败分别为全局 fake monotonic 影响真实子进程、旧 PID 存活夹具未释放新调用锁、入口夹具仍 mock 旧 subprocess 接口。修正测试边界后对应 3 项定向通过；没有改变真实预算或豁免 Gate。

完整复核 `78ccb021-896b-4b03-9d7d-d67f30e3297e` 使用任务起点 `00c29798ec106eb68b1de339fa3b0e87d1366418`，声明命令为 `D:/Projects-tools/AI-OS/.venv/Scripts/python.exe -B -m pytest tests -q -p no:cacheprovider --tb=short`（basetemp 为本任务系统临时目录）。329 项通过，318.35 秒；Finish completed/allowed=true，8 分项通过，Ruff、Git 差异和 Code Start 复核通过。Pyright 0 errors；提交前按逻辑单元对实际索引执行 Secret Scan。网络沿用官方 SSH 443 的进程级 insteadOf，不写持久配置，5 秒网络预算保持。

最终可审查包构建指纹 `cd018aba8ee557fc5f52242cfa05d8971caf891ec4ac2f50de92a2d3d1c94e36`。用 uv.lock 依赖独立安装后，带空格的 Windows launcher 建立新 stdio MCP 连接，8 工具、null change_class、只读 interrupted 查询均验证。隔离环境磁盘升级后旧 MCP 仍报告原 `bf1730aebd57...` 指纹，新连接才报告新构建。桌面安装/信任/重连未执行，不能把这次隔离连接称为宿主部署完成。已有交付包均保留。

门禁可靠性回归（REQ-GATE-RELIABILITY）：

- test_gate_reliability：31 项合成合法结构（24 CMake + 7 上游），隐藏于 build/vendor/探测目录的源码副本、来源不足、无配置/错误家族、盘符/UNC 诊断、远程选择和机器路径。
- test_finish_execution：真实命令与 Git/SSH 子孙进程、独立进程保留、超时/取消/真实 stdio 断连及查询；记录写入失败、重复编号、失去所有者、完成结果迟到取消等异常。
- test_plugin_skills/test_package：Python 与行内示例、CLI 示例、真实 MCP schema（仍 8 工具）、wheel/插件选择器及构建指纹一致；不恢复旧布尔参数。
- 修复前 174 项相关测试仅作历史基线。本轮先运行受影响测试，再完成一次完整 unit/integration 和包验收、ruff、pyright、Git 差异检查、Start/Finish 与拟提交索引 Secret Scan。

2026-10-02 实施验收：声明命令 `.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --tb=short`，298 项通过（123.37 秒）；ruff、pyright、Git 差异检查通过。Finish 执行编号 `694e5bb3-4e53-4fcd-8c7e-225fb8a83bc8`，8 分项通过，`completed` 且 `allowed=true`。任务基线仍为 `3b8e7fd70cc630e073b0bd85a92e7581294e639e`。

该次网络验证采用 Git 原生的一次性 `url.ssh://git@ssh.github.com:443/xingyuan-168/AI_Engineering_OS.git.insteadOf=git@github.com:xingyuan-168/AI_Engineering_OS.git`，选定远程和 upstream 都是 origin，实际探测同一仓库的官方 443 endpoint；未修改持久 Git 配置、未放宽 5 秒网络预算。此前默认 SSH 探测超时和新增夹具首次失败均保留在忽略运行记录中，不改写为通过；`37c841ef-5d77-4aa8-bd91-3a5493ae8bdd` 的 297 项成功不能消除其远程超时。

运行时 wheel 与插件构建指纹为 `70566df0abc0b750eb750c1dcf39b3b36180fed01326c00387f8ea294deb3677`。隔离安装后的新 stdio MCP 连接通过带空格的 Windows launcher 加载该构建，报告 8 工具并能查询已有超时记录；源码集另验证真实取消、断连及所属子孙进程终止。桌面安装、信任和重连未执行，当前宿主加载版本继续为未知；独立 DSH 安装保持原样。

补充验证脚本创建曾被实际 PreToolUse Hook 拒绝：目标为系统临时目录内 `aios-governance-repair-t4v2sp18/validate_delivery.py`，返回“input/ is read-only user input and copy-style version directories are forbidden”。未提供更具体命中路径或规则，未改用其他工具创建该脚本；后续只读包身份及状态查询是独立动作。

OpenCV 验证边界（仅只读核实和后续建议）：

- 9 月 20 日 Finish 和 10 月 1 日 Start 的历史阻塞均为原来 31 项；最终 Finish 未完成。后来额外目录单列，不改写历史数量。本轮不在那里运行测试、构建、Finish 或生成缓存。
- 历史 ZIP 文档差异是真实既有问题。后续分别验证源码卫生、候选交付、新发布和历史归档；历史基线须有可信源码提交，不能用归档自身证明。新包继续与当前源码一致。
- 性能保持 P95 ≤ 5 ms，保留原 5.3764 ms 失败及所有复测。现有真实透明基准可用；后续接入验收并记录请求/实际采样数、64 次预热、1000 次正式采样、平台与负载，串行覆盖透明、变化截图、命中/未命中。扩大采样是独立复测。
- 先核对 fixtures、Worker、工作目录；区分环境未就绪、历史证实的基线失败和新增回归。YOLO AUTO 分类来自历史旧 DLL/Worker 复现，本轮未重新复现；定向 CV 通过不等于全部 OCR/YOLO 通过，完整集真实失败仍阻断。

不在"full pytest"之后重复跑 plugin/agent/MCP 子集凑证据；一个逻辑变更跑它映射的用例即可。
