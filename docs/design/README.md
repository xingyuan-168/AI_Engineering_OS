# 设计文档中心

状态：可执行设计基线
适用范围：未来控制台、桌面端或 Web/App 项目；当前 CLI MVP 复用其中的信息架构和状态模型。

## 文档地图

- [USER_FLOW.md](USER_FLOW.md)：用户角色、主要任务和状态流转。
- [UX_RESEARCH.md](UX_RESEARCH.md)：用户画像、场景、痛点、参考对象和设计假设。
- [WIREFRAME.md](WIREFRAME.md)：控制台关键页面和低保真布局。
- [UI_SPEC.md](UI_SPEC.md)：页面行为、交互状态、可访问性和响应式规则。
- [COMPONENT_LIBRARY.md](COMPONENT_LIBRARY.md)：组件契约、变体和使用边界。
- [DESIGN_TOKEN.md](DESIGN_TOKEN.md)：颜色、字体、间距、尺寸和动效 Token。

## 设计门禁

涉及 Web、App、小程序或桌面应用时，必须按 `UX Research -> USER_FLOW -> WIREFRAME -> UI_SPEC -> COMPONENT_LIBRARY/DESIGN_TOKEN -> 前端实现` 顺序推进。缺少上游产物、验收标准或交互状态时，前端 Agent 必须保持阻塞。

## 当前 MVP 映射

CLI 的 `intake`、`status`、`approval`、`verify` 和 `release` 状态是未来控制台的导航和信息架构基线；不因当前没有图形界面而跳过状态、错误、空态和审批的设计。
