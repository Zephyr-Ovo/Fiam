# Studio Roadmap

> Studio 的角色：**工具台 / 创作间**。vault 是统一数据底（包括 track 产出），timeline UI 一份多用。
> 对称：人 ↔ AI 同等。可见性、编辑权由每条目自己声明，不靠服务关系。
>
> 写于 2026-05-14。CC 官方记忆已关，所以把决议沉淀在仓库。
> 现状参考 `STUDIO_DESIGN.md` §7.5。本文件只跟踪「下一步」。

## 约束

- 另一边正在改 `scripts/dashboard_server.py`，本计划期间**不要碰后端文件**，除非明确合并窗口。
- ISP（`/home/fiet/live/`）由 CC 在改，本地推进，不远端部署。
- 已落地：4 个 `/studio/*` endpoint + 测试 12 个、Atrium 扩展 popup、Obsidian 插件 MVP（timeline/desk/shelf/quick/co-author）、AI 私有 inbox 分离。

## Phase 0 — Vault 分级 & 元数据约定（设计先行，~半天）

只写文档，不写代码。是后面所有 phase 的基底。

- 目录结构定稿：
  - `shelf/`  书架（阅读材料、归档、长文）
  - `desk/`   书桌（创作仓库、草稿、Quick Note）
  - `track/`  记录官产出（编辑/工作/全系统时间线总结，分层 markdown）
  - `studio_ai_inbox/`（独立 repo） AI 私有收件箱
- 文件级 frontmatter 约定（新增字段）：
  ```yaml
  visibility: both | human | ai     # 谁能看见
  editable:   both | human | ai | none  # 谁能改
  authors:    [zephyr, ai]          # 历史作者集合
  ```
  默认 `visibility: both, editable: both`。
- 段落级落款约定：行尾注释 `<!-- @ai 12:34 -->` / `<!-- @zephyr 12:34 -->`。
  - 不精细到字符，按段落（空行分隔）。
  - 渲染时 plugin 可隐藏/显示。

**产出**：`STUDIO_CONVENTIONS.md`（新文件，权威约定）。

## Phase 1 — 记录官 v0 / track-model（核心，~2-3 天）

**为什么先做这个**：track 产出落进 vault，正好验证 Phase 0 的目录与 frontmatter；且数据源（git log）已存在，闭环最短。

- `fiam.toml` 新增段：
  ```toml
  [track]
  model      = "mimo-..."          # 默认模型
  api_env    = "MIMO_API_KEY"      # 环境变量名
  endpoint   = "..."
  ```
- 新模块 `src/fiam/track/`：
  - `collectors/edit.py`  — 从 studio vault `git log` 抽取编辑事件
  - `summarizer.py`       — 调 mimo，按 `#` / `##` / `###` 分层产出
  - `writer.py`           — 写入 `track/edit.md`（vault 内），带 frontmatter `visibility: both, editable: none`
  - `recall.py`           — `recall(name, since=None)` 函数，**时间衰减**：
    - 近 7 天：全文
    - 7–30 天：保留到 `##`
    - 30–90 天：保留到 `#`
    - >90 天：标题列表
- CLI：`python -m fiam.track run edit`，先手动触发。
- **不接 AI 工具调用、不接定时任务**——先把管道跑通。

**产出**：`src/fiam/track/` 模块 + `tests/test_track.py` + 文档。

## Phase 2 — 记录官扩展（~2 天）

- 新增 collector：
  - `collectors/work.py`   — fiam-code 仓库 git log（可按 commit message 前缀分项目）
  - `collectors/system.py` — pool/flow 事件（复用已有 store）
- `recall` 包装为 AI 工具（fiam channel tool schema），AI 调用按 name 取摘要，不全量读。
- 多 track 并存：`track/edit.md` / `track/work.md` / `track/system.md`。

## Phase 3 — Atrium 段落级落款（Obsidian 插件，~2 天）

依赖 Phase 0 落款约定。

- 插件 hook 编辑保存：识别被改动的段落（按空行切块 + 简单 diff），追加 `<!-- @<author> HH:MM -->` 标记。
- 作者来自插件 settings（`humanAuthor` 已有；AI 端写入时由 share 接口在 block 里带）。
- timeline view 增加「按段落作者高亮」开关。
- 共创区域不再整段 append——AI 直接 in-place 编辑就行，落款保证可追溯。

## Phase 4 — 可见性 / 编辑权运行时（~1 天）

依赖 Phase 0 元数据。

- 插件读 frontmatter `visibility`：`human` 文件 AI 视图隐藏，反之亦然。
- `/studio/share` 写入前检查目标 `editable`（**这一步要等后端合并窗口**，先在文档里挂着）。
- 默认 both，不破坏现有数据。

## Phase 5 — AI 端选段分享（~1 天）

- Atrium 浏览器扩展已经替人解决了"选段→share"。
- AI 端：在 channel runtime 加一个 helper（不是工具，是 helper），AI 输出里若包含 `:::share ... :::` 标记，runtime 自动 POST `/studio/share`，`source=ai`、`agent=<runtime_id>`。
- 不需要 AI 真的"选中"——他输出即选中。

## Phase 6 — Reader / 书架（~3-5 天）

- 找现成方案：候选 epub.js / Foliate / Readest。
- 整合方向：放进 Obsidian 插件作为子 view，或独立桌面 app 嵌进 Atrium。
- 阅读时长统计 → 落到 `track/reading.md`（自然复用记录官管道）。
- 批注 → 写 `shelf/<book>/annotations.md`。

## Phase 7 — 美化（~按兴趣）

- 网页预览：把 `shelf/` 里有 `url:` frontmatter 的条目用 microlink/og 抓预览图。
- 卡片化渲染，timeline 节点嵌图。

## Phase 0.5 — Fiam 接入（delivery policy，小，~半天）

复用现成机制，**不要新发明**。`src/fiam/turn.py:36` 已有 `DeliveryPolicy = record_only | lazy | instant | batch | state_only`，`plugins/{browser,email,ring}/plugin.toml` 已在用 `delivery = "lazy"`。

- 新建 `plugins/studio/plugin.toml`：
  ```toml
  id = "studio"
  name = "Studio vault"
  delivery = "record_only"   # 写就进历史，但绝不叫醒 AI
  receive_channels = ["studio"]
  ```
- `/studio/share` 写完后向 fiam 注入一条 `record_only` 事件（channel=studio，actor=zephyr|ai，payload 带 rel_path、source、commit_sha）。
- 准则：**人侧 Studio 操作绝不触发 AI**；AI 侧 Studio 操作天然走它自己的 tool-use 入流，不重复记。
- ⚠️ 接入点在 `scripts/dashboard_server.py`，**等另一边后端 bugfix 合并后再做**，先把 plugin.toml 写好挂着。

## Atrium ↔ Obsidian 联动（贯穿性，非独立 phase）

澄清角色——避免后续混淆：

- **Atrium**（已有，`plugins/atrium/`、`channels/atrium/`）= Zephyr PC 上的 Tauri 桌面 host，已到 M12（浏览器扩展桥、窗口/进程/Web 拦截、reader/co-reader surface）。**它不是编辑器**，是「手和眼」。
- **Obsidian + fiam-studio 插件** = 编辑/阅读/timeline 渲染的人机共享 surface。
- **Studio vault** = 共享数据底（git repo）。

Obsidian 桌面端能做的事（盘点天花板）：
- 任意 HTML/CSS/SVG/Canvas、custom view/modal、code-block processor、reading-view processor
- Canvas → PNG = 生成海报；交互按钮/表单 = 普通 DOM；可嵌 React/Svelte
- 桌面：`fetch`、`child_process`、文件读写
- **不行**：系统级原生 API、注册系统全局快捷键、OS 级悬浮窗、深度截屏/剪贴板钩子
- 移动端：受 Obsidian mobile app 限制，无 `child_process`，无原生集成

**联动分工**：
- 编辑、阅读、timeline 渲染、海报生成（Canvas）、批注 UI → Obsidian 插件
- 系统级动作（深度截屏、跨应用复制、注册快捷键、Web 拦截送 share、OS 通知）→ Atrium Tauri
- 协议：
  - Atrium → Obsidian：`obsidian://open?vault=fiam-studio&file=...`（已计划，P0 后试一次）
  - Obsidian → Atrium：插件 `fetch` 本机 Atrium HTTP 端口（与 MQTT 桥同接口）
  - 两者共享：同一 vault 路径 + 同一 `/studio/*` API

不为联动单开 phase——在 P3（段落落款）/P5（AI 分享）/P6（Reader）里随手做即可。

## 推进顺序（一行版）

```
P0 约定 → P0.5 fiam接入(挂着) → P1 记录官v0 → P2 记录官扩展 → P3 段落落款 → P4 权限 → P5 AI分享 → P6 Reader → P7 美化
```

P0 → P1 是关键路径。P0.5 的 plugin.toml 部分可与 P0 同期写；接入点等后端合并窗口。P3 可与 P2 并行（不同文件）。P6 体量大、可拖到后期。

## 进度

- [x] P0 约定文档 — `STUDIO_CONVENTIONS.md`
- [~] P0.5 fiam delivery 接入 — `plugins/studio/plugin.toml` ✅；后端注入点等合并窗口
- [x] P1 记录官 v0 — `src/fiam/track/` (config/collectors/summarizer/writer/recall + CLI) · `tests/test_track.py` 12 测试
- [x] P2 记录官扩展 — `collectors/work.py` + `collectors/system.py` + `summarize_system` + `Recall` tool in API runtime · 22 测试
- [ ] P3 段落落款
- [ ] P4 权限运行时
- [ ] P5 AI 端分享
- [ ] P6 Reader
- [ ] P7 美化
