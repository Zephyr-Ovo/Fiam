# Favilla

> a spark -> fiam

Favilla 是 fiam 的随身入口：主屏用于和 Fiet 对话，也保留划词捕获、悬浮窗共读和状态检查。

## 设计

- **Chat**：主屏直接发送消息到后端，当前内置 `cc` 后端，也可填写自定义 API URL。
- **Recall display**：服务端返回 `recall` 时，App 会把它显示在 Fiet 侧的会话里。
- **PROCESS_TEXT intent**：在系统文本选中菜单里加一项 "Send to fiam"。
- **SEND intent**：在任何 app 的"分享到..."里出现 Favilla。
- 选中 -> POST 到 `{server}/api/capture` -> MQTT `fiam/receive/favilla` -> daemon 写入 flow。

## 首次使用

1. 装 APK（见下）
2. 打开 Favilla -> 填 Server URL（`https://fiet.cc`）+ Ingest Token -> Save。
3. Backend 默认选 `Claude Code`；如果要接自己的服务，选 `Custom API` 并填完整 URL。
4. 在 Chat 里发消息；Stats 按钮确认 token 和服务状态 OK（不会写入 flow，也不会唤醒 AI）。
5. 在任何 app 里：长按文字 -> 选 Send to fiam。
6. 共读时点 Start readalong，选中文本后点悬浮按钮；结束时点 End readalong。

## 构建

本地无需 Android 工具链。push 到仓库后 GitHub Actions 自动构建 debug APK，在 Actions artifact 里下载。

手动本地构建需要：
- JDK 17
- Android SDK（`sdkmanager "platforms;android-34" "build-tools;34.0.0"`）
- `./gradlew assembleDebug` → `app/build/outputs/apk/debug/app-debug.apk`

## Capture 字段

POST body:
```json
{
  "text": "required string",
  "source": "android",
  "tags": ["optional"],
  "url": "optional",
  "kind": "interaction",
  "interaction": "weread",
  "session_id": "optional",
  "phase": "start|end|optional"
}
```

Status check:

```http
GET /api/app/status
X-Fiam-Token: <ingest token from server env>
```

Headers:
```
Content-Type: application/json
X-Fiam-Token: <ingest token from server env>
```

## Chat API

内置 `cc` 后端由 dashboard server 提供：

```http
POST /api/app/chat
X-Fiam-Token: <ingest token from server env>
Content-Type: application/json
```

```json
{
  "backend": "cc",
  "text": "required string",
  "source": "android",
  "session_id": "app-local-session-id"
}
```

成功返回：

```json
{
  "ok": true,
  "backend": "cc",
  "reply": "assistant reply",
  "recall": "optional recall text",
  "session_id": "claude-session-id",
  "cost_usd": 0.0
}
```

自定义 API URL 由 App 直接 POST，同样发送 `text`、`source`、`session_id`，并接受 `reply` 或 `text` 字段作为回复。

## 图标

Placeholder adaptive-icon（深色背景 + 暖色余烬火光）。
