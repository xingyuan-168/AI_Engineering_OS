# 组件库基线

状态：可执行设计基线

## 组件目录

| 组件 | 变体 | 必备状态 | 使用边界 |
| --- | --- | --- | --- |
| `AppShell` | 桌面、移动 | 加载、断线 | 统一项目上下文和导航 |
| `StatusBadge` | ready/running/blocked/failed/approved/completed | 默认、禁用 | 必须显示文字，不单靠颜色 |
| `StageStepper` | 横向、纵向 | 当前、完成、阻塞 | 只表示 Workflow 阶段，不代替任务列表 |
| `TaskTable` | 紧凑、可展开 | 加载、空、错误 | 任务字段固定，长文本折行 |
| `EvidenceList` | 文档、日志、测试 | 缺失、已验证 | 每条证据必须有来源链接和时间 |
| `ApprovalPanel` | 普通、高风险 | 待审批、已批准、已退回 | 高风险操作必须显示影响和二次确认 |
| `CommandPreview` | dry-run、已执行 | 阻止、成功、失败 | 显示命令、路径、退出码和审批 |
| `EventTimeline` | 压缩、完整 | 新事件、失败 | 事件按时间排序，不可静默修改 |
| `EmptyState` | 无项目、无任务、无证据 | 默认 | 必须提供下一步动作 |
| `ErrorState` | 可恢复、不可恢复 | 默认 | 必须显示错误类别和恢复入口 |

## 组件契约

组件必须定义输入属性、输出事件、键盘行为、加载/空/错误状态、可访问名称和移动端行为。组件不得直接调用 Workflow 或 Execution 服务；通过页面容器传入数据和回调。

## 组合规则

- 页面可组合 `AppShell + StageStepper + TaskTable + EvidenceList`。
- `ApprovalPanel` 只能出现在门禁上下文，不嵌套在另一个卡片中。
- `CommandPreview` 不能隐藏在不可展开的提示框中。
- 表格和时间线使用稳定尺寸，加载期间不能导致布局跳动。
