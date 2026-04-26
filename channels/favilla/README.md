# Favilla

Favilla is the Android companion app for Fiam. It is chat-first, but it also acts as the phone capture surface for text, images, future voice, readalong bubbles, and quick state markers.

## Current App

- **Chat**: sends messages to the built-in `/api/app/chat` backend or to a user-provided custom API URL.
- **Hub**: safety switch for AI control markers, voice placeholder, image picker, and quick marker buttons.
- **Stats**: token-protected `/api/app/status` health panel. It reads daemon/store counts and does not write to flow.
- **More**: backend/token/source settings, custom chat API, Vision/STT/TTS routing entries, overlay/accessibility/camera/mic permissions, bubble/readalong controls.
- **PROCESS_TEXT / SEND intents**: selected/shared text posts to `/api/capture`, then MQTT `fiam/receive/favilla`, then daemon/Conductor write flow beats.

Version: `0.3.3`, package `cc.fiet.favilla`, app id `cc.fiet.favilla`.

## Multimodal Contract

- Text chat and selected text enter flow as text beats.
- Voice input is reserved for STT; the transcript will enter flow as a normal text beat.
- Images are routed to a separate vision provider. The vision result enters flow as `kind="action"` text, so the main chat AI does not receive raw image bytes.
- `stroll` / `散步` is the reserved ambient mode: wearable/camera context -> vision text -> AI may speak through TTS.

The current image/voice UI is a routing placeholder. Real Vision/STT/TTS provider calls still need to be wired.

## Local Storage

Sensitive local values are stored in `SharedPreferences` encrypted with an Android Keystore AES-GCM key:

- ingest token
- Vision API key
- STT API key
- TTS API key

Non-secret routing values such as server URL, model name, and source tag remain plain local preferences. Old plaintext secret values migrate to encrypted form on first read.

## First Use

1. Install the debug APK.
2. Open Favilla -> More -> set Server URL, usually `https://fiet.cc`, and the ingest token.
3. Keep backend on `Claude Code` for `/api/app/chat`, or choose `Custom API` and enter a full endpoint URL.
4. Use Chat for conversation, Stats for backend status, Hub for markers/images/voice stubs.
5. For readalong, enable overlay/accessibility permissions, start the bubble, then use selected text or the floating action.

## Build

Preferred path: build through GitHub Actions or a local Android toolchain with Gradle wrapper/Gradle installed.

Manual local build needs:
- JDK 17
- Android SDK（`sdkmanager "platforms;android-34" "build-tools;34.0.0"`）
- `./gradlew assembleDebug` → `app/build/outputs/apk/debug/app-debug.apk`

## Capture Body

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

Custom API URL is called directly by the app. It receives `text`, `source`, and `session_id`, and the app accepts either `reply` or `text` in the JSON response.

## Icon

The app icon and avatar use the user-provided `avatar_ai.png` peach spark. Adaptive foreground assets are padded for Android's safe zone; legacy launcher PNGs are generated at density-correct sizes.
