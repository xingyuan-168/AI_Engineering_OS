# 设计 Token

状态：可执行设计基线
主题：中性、工作台式、强调证据和状态；不使用装饰性渐变。

## 色彩

```yaml
color:
  bg: "#F7F8FA"
  surface: "#FFFFFF"
  text: "#1F2937"
  text_muted: "#667085"
  border: "#D0D5DD"
  primary: "#155EEF"
  info: "#0E7490"
  warning: "#B54708"
  danger: "#B42318"
  success: "#027A48"
```

## 字体和间距

```yaml
font:
  family: "Inter, Microsoft YaHei, sans-serif"
  body: 14px
  small: 12px
  heading_lg: 24px
  heading_md: 18px
  line_height: 1.5
space:
  1: 4px
  2: 8px
  3: 12px
  4: 16px
  5: 20px
  6: 24px
  8: 32px
```

## 尺寸和形状

```yaml
radius:
  sm: 4px
  md: 6px
  lg: 8px
control:
  height: 40px
  compact_height: 32px
focus_ring: "0 0 0 3px rgba(21, 94, 239, 0.24)"
```

## 动效

- 默认过渡 150ms，主要用于状态和展开，不用于装饰。
- 失败、阻塞和审批不使用闪烁动画。
- 支持 `prefers-reduced-motion`，减少或关闭非必要动画。

## Token 验收

所有 UI 颜色、字号、间距、圆角和焦点样式必须引用 Token；新增直接值必须说明用途并加入本文件，禁止组件各自定义同义颜色或间距。
