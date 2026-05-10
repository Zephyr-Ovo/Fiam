# UI Plan — Favilla / Dashboard

> 工具选型、素材源、迭代流程。这份是给 Claude 接手 UI 工作时的入口。

## 0. 现状

- Favilla（Android 伴侣 App）：Material3 + ViewBinding + 自定义字体（Anthropic Sans/Serif/Mono + Noto Serif SC）。布局已基本可用，需继续打磨。
- Dashboard（SvelteKit）：已 bundled Anthropic 字体 + CJK fallback。
- 当前刚上线 CoT 可见性（💭/🔒），是第一个新 UI 组件——Zephyr 可以拿这个当样张迭代。

## 1. 设计工具（Zephyr 试用清单）

按"导出物对 AI 友好度"排序。**用户已表示要试 1/2/4。**

### ① Penpot — https://penpot.app
- 开源 + 自托管或免费 SaaS。可视化编辑接近 Figma。
- 导出：**SVG（per artboard）+ Design Tokens JSON**。AI 可以直接读 SVG 几何 + 颜色变量。
- 推荐流程：在 Penpot 画 mockup → Export SVG + tokens.json → 提交到 `docs/design/<screen>.svg` + `docs/design/tokens.json` → Claude 翻译成 Compose XML / Svelte 组件。
- CJK 字体可在自托管版上传。

### ② Figma 免费版 — https://figma.com
- 工业标准；插件/资源生态最大。
- 导出：SVG / PNG；通过 plugin 可以导出 JSON Schema、Code Connect、Tokens Studio for Figma。
- AI 友好程度：**只导出 PNG 时差**，**配合 Tokens Studio + 插件 "Figma to JSON"** 后接近 Penpot。
- 用 Anima / Locofy 插件可生成 React/Flutter 代码（可作 prototyping，不直接落地到 Favilla）。

### ④ FlutterFlow — https://flutterflow.io / Subframe — https://subframe.com
- **FlutterFlow**：可视化拖拽生成 Flutter 项目；适合做"独立 prototype 验证交互"，导出 Dart 代码。**注意：Favilla 现在是 Kotlin 原生，迁 Flutter 会重写。** 建议只用来探索新页面/动效。
- **Subframe**：visual builder → 直接生成 React + Tailwind 组件代码。**dashboard 是 SvelteKit**，不直接吻合，但生成出来的 JSX 可以人工/AI 翻译成 Svelte。

### （跳过）③ tldraw / Excalidraw — 用户已表示不试
- 适合白板草图阶段，导出 PNG/SVG。如果以后想白板 brainstorm 再考虑。

## 2. 素材源

### 图标
- **Lucide** — https://lucide.dev — 简洁、与 shadcn/ui 同源、SVG 单色
- **Phosphor** — https://phosphoricons.com — 6 种粗细（thin → fill），表达力强
- **Tabler** — https://tabler-icons.io — 4000+ 图标，统一线条
- **Iconify** — https://icon-sets.iconify.design — 聚合站，搜任意图标包
- **Material Symbols** — https://fonts.google.com/icons — 与 Material3 原生匹配，Favilla 可直接 `@drawable` 引入

### 插画
- **unDraw** — https://undraw.co — 可改色 SVG，免署名
- **Open Peeps** — https://openpeeps.com — 手绘人物拼贴
- **Humaaans** — https://humaaans.com — 同类，姿态丰富
- **DrawKit** — https://drawkit.com — 高质量插画，部分免费
- **Storyset** — https://storyset.com — Freepik 旗下，可调色/动效

### 字体（已 bundled）
- Anthropic Sans / Serif Italic / Mono — 仓库内 `dashboard/static/fonts/` & `channels/favilla/app/src/main/res/font/`
- Noto Serif SC（中文）— 同上
- 备用：Inter (sans), Source Serif Pro, JetBrains Mono — 通过 https://fontsource.org 拿 `.ttf`

### 配色 / 设计 token
- **Tailwind palette** — https://tailwindcss.com/docs/customizing-colors — 可直接抄
- **Material3 color builder** — https://m3.material.io/theme-builder — 给一个 seed color，生成 light/dark + container/surface/onPrimary 全套
- **Coolors** — https://coolors.co — 配色生成器
- 当前 Favilla 的 `colors.xml` 是手工调，未来引入 token 后可对齐

## 3. 推荐工作流（Zephyr → Claude）

```
Zephyr in Penpot/Figma
  ↓ export
docs/design/<screen>/
  ├── mockup.svg          (Penpot/Figma SVG)
  ├── tokens.json         (颜色/spacing/字号)
  └── notes.md            (交互意图、变体说明)
  ↓ commit / paste
Claude:
  1. 读 SVG 量出尺寸/层级
  2. 对照 tokens.json 生成 colors.xml / app.css 增量
  3. 翻译成 Compose XML 或 Svelte 组件
  4. 起 Actions build → adb 装机 → screencap 验证
```

如果只有 PNG 截图也可以直接发，但 AI 测量精度会下降（颜色靠肉眼，间距估算）——SVG > PNG。

## 4. 优先级 backlog

1. CoT 气泡视觉打磨（💭 颜色、点击态、展开动画）— 第一份样张候选
2. Splash 改进（当前是单行文字）
3. Hub 标签快捷区视觉层次
4. Stats 指标卡片
5. Settings 分组的视觉密度

## 5. 不做（先记下）

- 改主导航结构（底部 Chat/Hub/Stats/More 已经稳定）
- 重新挑字体家族（Anthropic + Noto Serif SC 已成型）
- 引入设计系统库（如 MaterialKolor）— 等 token JSON 流程跑通再说
