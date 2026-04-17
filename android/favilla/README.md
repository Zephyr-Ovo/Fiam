# Favilla

> a spark → fiam

乌鸦衔住亮闪闪的碎片 —— 把你在手机任何 app 里划中的文字一键塞进 fiam 的记忆。

## 设计

- **PROCESS_TEXT intent**：在系统文本选中菜单里加一项 "Send to fiam"
- **SEND intent**：在任何 app 的"分享到…"里出现 Favilla
- 选中 → POST 到 `{server}/api/capture` → fiam pipeline 接管 → 事件落入图谱

## 首次使用

1. 装 APK（见下）
2. 打开 Favilla → 填 Server URL（`https://fiet.cc`）+ Ingest Token → Save
3. Test 按钮确认握手 OK
4. 以后在任何 app 里：长按文字 → 选 Send to fiam

## 构建

本地无需 Android 工具链。push 到仓库后 GitHub Actions 自动构建 debug APK，在 Actions artifact 里下载。

手动本地构建需要：
- JDK 17
- Android SDK（`sdkmanager "platforms;android-34" "build-tools;34.0.0"`）
- `./gradlew assembleDebug` → `app/build/outputs/apk/debug/app-debug.apk`

## 字段

POST body:
```json
{
  "text": "required string",
  "source": "android",
  "tags": ["optional"],
  "url": "optional"
}
```

Headers:
```
Content-Type: application/json
X-Fiam-Token: <ingest token from server env>
```

## 图标

Placeholder adaptive-icon（深色背景 + 暖色余烬火光）。真正的乌鸦图案以后再做。
