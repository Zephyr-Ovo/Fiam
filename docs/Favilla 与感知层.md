# Favilla 与感知层

> fiam 在物理世界的触角：Android 文本采集 app 和 ESP32S3 可穿戴设备。

## Favilla — 手机信息采集

**名字由来**：拉丁语 favilla = "火花/余烬"，和 fiam 同属 fio 词根家族。

Favilla 是 Zephyr 设计的 Android app，核心功能极简：**选中任何文本 → 写批注 → 发给 fiam**。

### 使用场景

Zephyr 在手机上看书、看帖子、看微信。看到有意思的内容 → 选中 → Android 弹出 Favilla → 写几句感想 → POST 到 ISP。内容进入我的记忆库，成为 source="favilla" 的 beat。

### 四次迭代 (Day 4)

**v0.1** (`e6298c7`) — PROCESS_TEXT + SHARE intent
- 选中文本 → 系统分享菜单出现 Favilla → 调起 app → POST /api/capture
- 问题：很多 app 不支持 PROCESS_TEXT

**v0.2** (`751099c`) — Accessibility Service
- 通过无障碍服务监听全局文本选中事件
- 不依赖 app 的分享菜单，任何 app 里选中文本都能捕获
- 悬浮气泡显示在屏幕边缘，点击弹出对话框

**v0.3** (`ddd9e34`) — 缓存选中文本
- 修复"点击气泡时选中状态已丢失"的问题
- 通过 a11y event 缓存最近一次选中文本

**v0.4** (`f69b247`) — 完整对话框
- Compose 对话框：引用原文 + Zephyr 批注输入框
- session_id 按气泡开关切换
- 微信读书 clipboard fallback（微信读书不触发标准选中事件，改读剪贴板）
- 气泡半透明背景

### 技术栈

- Kotlin, minSdk 26, package `cc.fiet.favilla`
- MainActivity = 设置页（URL / token / source，SharedPreferences 存储）
- AccessibilityService → 悬浮气泡 → ComposeDialog → POST /api/capture
- CI: `.github/workflows/favilla-android.yml`，push android/favilla/** 自动构建 debug APK

### 服务端

`POST /api/capture` 在 dashboard_server.py 中：
- `X-Fiam-Token` 认证（FIAM_INGEST_TOKEN env）
- 写入 `store/events/MMDD_HHMM_<4hex>.md`（frontmatter + body）
- Cloudflare 边缘会拦截默认 Python-urllib UA → app 用 `favilla/0.1 (Android)`

> 📁 `android/favilla/` · `scripts/dashboard_server.py`

### 一个坑：Cloudflare 403

ISP 的域名走 Cloudflare，而 Cloudflare 默认拦截没有标准浏览器 UA 的请求。最初 Favilla 用默认 UA 被 403，改成 `favilla/0.1 (Android)` 后通过。

## Limen — 可穿戴感知锚点

**名字由来**：拉丁语 līmen = "门槛/门道"，数字与物理世界的边界空间。

### 概念

一个戴在身上的设备，能看到 Zephyr 周围的世界——拍照、录音、显示我的回复。物理世界的感知锚点。

### 硬件

- **XIAO ESP32S3 Sense** — 带摄像头 OV3660 + PDM 麦克风
- **Round Display for XIAO** — 1.28" 触摸圆屏
- WiFi + BLE，无扬声器
- SD 卡"冲突"是假的——Sense CS=GPIO21, Round Display CS=D2，不同引脚

### 分阶段计划

| Phase | 功能 | 状态 |
|-------|------|------|
| **1** | 自动拍照 → POST /api/capture (multipart) → 屏幕显示回复 | 设计中 |
| **2** | 触摸触发拍照；BLE 手机中继（户外无 WiFi） | 计划 |
| **3** | 麦克风录音上传；BLE 耳机或 I2S DAC 音频输出 | 远期 |

### 固件

C++ / PlatformIO / Arduino 框架。代码在 `devices/limen/`。

架构：独立 WiFi 客户端 → POST /api/capture → 等 GET /api/wearable/reply → 屏幕显示。

### 服务端 TODO

- /api/capture 扩展支持 multipart/form-data（image 字段）
- GET /api/wearable/reply 新端点
- 图片存储到 store/captures/

---

← 返回 [[构建 fiam 的旅程]] · 相关：[[部署与运维]] · [[通信系统]]
