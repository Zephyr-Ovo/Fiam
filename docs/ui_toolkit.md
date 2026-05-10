# Favilla UI Toolkit（资源索引）

> 来源：Zephyr 与 Web Claude 的设计对话（2026-04-26）
> 用途：素材/库/学习资源的速查表。本文不是路线图，路线图见 [`app_plan.md`](app_plan.md)。

---

## 0. 框架决策（重要）

**当前实现**：Favilla = 原生 Android Kotlin（`channels/favilla/`）。
- 已落地：Material3 + ViewBinding 自定义 NavigationRail（9 项）、ChatFragment、HubFragment、CaptureClient、CoT toggle。
- 已通过 GitHub Actions 真机验证，可滚动侧边栏 + 占位 Fragment 全部 OK。

**Web Claude 建议**：Expo (React Native) 全跨平台。

**取舍备忘**：
| 维度 | 保留 Kotlin | 切到 Expo |
|---|---|---|
| 已写代码 | 全部复用 | 全部丢弃 |
| iOS 同步 | 不行 | OK（你只在 Android，所以 0 价值） |
| 摄像头/通知/前台服务 | 一类 API，最稳 | Expo 模块大多够用，但前台 service 仍要 native |
| 动画素材 | XML/Compose，CodePen/RN 库都用不上 | 直接吃所有 RN 生态 |
| AI 协助密度 | 中等 | 高 |
| 学习成本 | 你已熟 | 要从零搭 |

**结论**：
- **不切**。Favilla 继续 Kotlin。Expo 资源作为"长 schedule 重写"的备选档案保留。
- 若后续要做 web dashboard 的二次升级，可单独建 Expo Web，不影响 Android。
- 设计语言、配色、动效原则（Part 7）跨框架通用，下面整理时按"概念"而非"代码包"组织。

---

## 1. 设计语言（跨框架通用）

### 1.1 学习材料
- **Refactoring UI** — https://www.refactoringui.com/  开发者向 UI 圣经，YouTube 有免费精华。
- **Laws of UX** — https://lawsofux.com/  21 条法则，5 分钟看完。
- **Typescale** — https://typescale.com/  字号比例生成器。
- **Coolors** — https://coolors.co/  配色生成。
- **Realtime Colors** — https://www.realtimecolors.com/  在真实 UI 预览配色。
- **Happy Hues** — https://www.happyhues.co/  预设方案 + 真实网站预览。

### 1.2 字体
- **Google Fonts** — https://fonts.google.com/
  - UI 英文：Inter
  - 代码：JetBrains Mono
  - 中文：Noto Sans SC（已用 / 已绑定 Favilla 字体）

### 1.3 图标
- **Lucide** — https://lucide.dev/  当前 Favilla XML drawable 风格的来源参考。
- **Phosphor** — https://phosphoricons.com/  6 种粗细。
- **Tabler Icons** — https://tabler.io/icons  5000+。

### 1.4 插画/Lottie
- **unDraw** — https://undraw.co/  可改色 SVG。
- **LottieFiles** — https://lottiefiles.com/  JSON 动画，Android 可用 `airbnb/lottie-android`。

### 1.5 音效
- **Freesound** — https://freesound.org/  CC 协议，鸟鸣/雨/风。
- **Mixkit** — https://mixkit.co/free-sound-effects/

---

## 2. 自然动效素材（CodePen 启发，需自行移植到 Android）

| 主题 | 链接 | Android 落地建议 |
|---|---|---|
| 雨（CSS） | https://codepen.io/arickle/pen/XKjMZY | Compose 的 `Canvas` + 协程粒子 |
| 雪 | https://codepen.io/NickyCDK/pen/DePKey | 同上 |
| 水/波浪 | https://devsnap.me/css-water-effects | 走 Lottie 更省事 |
| 粒子 | https://particles.js.org/  https://vincentgarreau.com/particles.js/ | Android 推荐自实现轻量粒子或用 Lottie |

**Android 等价方案**：所有上面这些都可以用 `Canvas` 自绘 / Lottie 播放 / `MotionLayout` 实现，不要硬翻 CSS。

---

## 3. 阅读器（Reading 模块）

**目标**：Favilla 左侧 nav 已留 `Reading` 占位。

### 3.1 引擎候选
- **foliate-js** — https://github.com/johnfactotum/foliate-js  轻量 EPUB 渲染，可包进 WebView。
- **epub.js** — https://github.com/futurepress/epub.js  老牌选手。
- **Readium Mobile (Kotlin SDK)** — https://readium.org/  原生方案，最稳但重。

**首选**：foliate-js + Android `WebView`，简单且批注 JSON 可控。

### 3.2 Obsidian 同步
- 协议：批注存为 JSON → push 到 Obsidian vault（GitHub repo 或 iCloud/云盘）。
- App 直接 `git pull` 或读云盘文件。
- 字段：
  ```json
  {
    "book_id": "isbn_or_hash",
    "cfi": "epubcfi(/6/14!/4/2/1:0)",
    "text": "选中文本",
    "note": "批注",
    "timestamp": "2026-04-26T00:00:00Z",
    "source": "favilla_reader"
  }
  ```
- 后端：可走当前 `dashboard_server.py` 加 `/reader/notes` endpoint，或独立 vault-sync 服务。

---

## 4. 小游戏 / 娱乐层

> 都是 Web/JS 项目，Android 落地用 WebView 内嵌或重写。

### 4.1 虚拟宠物
- **tugcecerit/Tamagotchi-Game** — https://github.com/tugcecerit/Tamagotchi-Game  纯 HTML/CSS/JS，最容易 WebView 嵌入。
- **PoterekM/tamagotchi** (React) — https://github.com/PoterekM/tamagotchi
- 3D 路线：搜 GitHub `threejs tamagotchi virtual-pet`。

### 4.2 棋类
- **chess.js + chessboard.js** — https://github.com/jhlywa/chess.js  轻量引擎，对手可走 Claude API。
- **3D Hartwig Chess** (CodePen) — 视觉参考。

### 4.3 集合
- https://github.com/michelpereira/awesome-open-source-games
- https://www.edopedia.com/blog/open-source-html5-and-javascript-games/

---

## 5. Dashboard / 数据可视化

> 当前 Favilla `Dashboard` nav 是占位。决策：**继续走 web dashboard**（`scripts/dashboard_server.py` + `dashboard/` Svelte），Favilla 只做嵌入式 WebView 视图。

### 5.1 Web 库（用于 `dashboard/`）
- **Recharts** — https://recharts.org/
- **Nivo** — https://nivo.rocks/
- **tremor** — https://www.tremor.so/  Tailwind dashboard 组件。
- **React Grid Layout** — https://github.com/react-grid-layout/react-grid-layout  可拖拽。

### 5.2 设计灵感
- Dribbble: `AI dashboard` / `personal assistant UI` / `ambient display` / `smart home dashboard`
- Figma 社区：搜 `dashboard template` / `AI chat interface`

---

## 6. 给 AI 的设计 Brief 模板

```markdown
## 页面：[名称]
## 用途：[做什么]
## 情绪：[温暖/科技/极简/赛博朋克/自然...]
## 参考：[Dribbble/Behance 链接 或 截图]

## 布局：
- 顶部：…
- 主体：…
- 底部：…

## 组件：
- [ ] 聊天气泡（圆角，时间戳）
- [ ] 状态卡片（情绪/电量）
- [ ] 动效背景

## 配色：
- 主色：#…
- 背景：…
- 强调色：#…

## 交互：
- 点击：…
- 长按：…
```

**给 AI 素材的最佳方式**：
1. 截图 + Figma/Excalidraw 标注 → 直接发。
2. 引用具体组件名（"shadcn Card + Aceternity Background Gradient"）。
3. 贴现有代码 + 改造指令。
4. 创建 Pinterest/Dribbble board 收集情绪板。

---

## 7. 费用记录

| 项 | 现状 | 备注 |
|---|---|---|
| 服务器 | $0 | GCP credits / 现有 fiet.cc VPS |
| Claude API | $0 | Vertex AI 走 GCP credits |
| Favilla 编译 | $0 | GitHub Actions 公仓 |
| Twilio（语音） | ~$1.15/月 | 见 [`voice_plan.md`](voice_plan.md) |
| 域名 | $0 | GitHub Student Pack |
| App Store | 不上架 | 无需 $99/年 |
| **总计** | **~$1.15/月** | |

---

## 8. 与现有 Favilla nav 的映射

| Nav 项 | 当前状态 | 本文档对应章节 | 下一步动作 |
|---|---|---|---|
| Home | 占位 | §1 设计 + §2 动效 | 选一个 valence-arousal + 动效背景做 hero |
| Chat | ✅ 已实现 | §1 设计微调 | CoT toggle 已上，下一轮做样式打磨 |
| Reading | 占位 | §3 阅读器 | foliate-js 嵌入 WebView |
| Dashboard | 占位 | §5 Dashboard | WebView 嵌入 `dashboard/` |
| Stroll | 占位 | §2 动效 + Camera | `expo-camera` 等价：CameraX |
| Reminder | 占位 | — | 走 `WorkManager` |
| Phone | 占位（HubFragment） | [`voice_plan.md`](voice_plan.md) | Twilio 接入 |
| History | 占位 | §5 Dashboard | 时间线视图 |
| Settings | ✅ MoreFragment | — | — |

---

*Last updated: 2026-04-26*
