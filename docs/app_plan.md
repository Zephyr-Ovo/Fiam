# fiam App Plan

目标：把手机端从“零散入口”收束成一个稳定的随身 fiam 客户端。App 不做记忆推理，不直接写 Pool，不绕过 Conductor；它只负责采集、展示、交互、通知，所有信息仍进入 `flow.jsonl`。

## 当前基线

- `fiet.cc` 已通过 Cloudflare Tunnel 暴露，Caddy 做 Basic Auth，dashboard backend 在 `127.0.0.1:8766`。
- `/api/capture` 绕过 Caddy Basic Auth，由 `FIAM_INGEST_TOKEN` 保护，适合 Favilla/移动端写入。
- `/api/capture` 已改成 HTTP -> MQTT：发布到 `fiam/receive/favilla`，daemon 订阅后交给 Conductor 写 flow 和 frozen beat vectors。
- Favilla Android 已支持分享、选中文本、悬浮按钮、批注输入；它是当前 App MVP 的采集子集。

## 产品边界

- PWA/web client 优先：先把聊天、状态、手动切分、简单通知做在 web dashboard 里，减少原生维护成本。
- Android native 只承接系统能力：PROCESS_TEXT、Share Sheet、悬浮按钮、通知、后台轻量 capture。
- AI 推理和记忆计算留在服务器侧：手机端不跑 embedding，不保存长期原始敏感数据。
- 所有入口统一成 beat：`source=app|favilla|voice_call|ring|limen`，正文放 `text`，路由、url、tags、设备信息放 `meta`。

## 协议

### Inbound

MVP 继续使用：

```http
POST /api/capture
X-Fiam-Token: <device_or_ingest_token>
Content-Type: application/json
```

```json
{
  "text": "required",
  "source": "app",
  "url": "optional",
  "tags": ["optional"],
  "meta": {"optional": true}
}
```

Server side publishes to MQTT:

```text
fiam/receive/<source>
```

The daemon decides delivery according to `ai_state`: `notify` wakes, `mute|busy` queues, `block` records without delivery, `sleep` queues until wake/open.

### Outbound

Initial web/PWA client can poll or fetch dashboard APIs. Realtime app dispatch should later use one server-owned stream:

- `GET /api/app/inbox` for pending outbound messages.
- `POST /api/app/ack` to mark delivered/read.
- Later: SSE/WebSocket for live dispatch, backed by MQTT `fiam/dispatch/app`.

Do not expose raw MQTT to phone clients on the public internet.

## Auth

- Human dashboard viewing: Caddy Basic Auth now; Cloudflare Access can replace it later.
- Write-only capture: `FIAM_INGEST_TOKEN` now; move to per-device tokens before wider use.
- Per-device token format: random 32-byte token shown once, server stores hash + label + created/revoked time.
- Native app stores token in Android encrypted storage, not plain SharedPreferences, before real daily use.

## Build Phases

### Phase 0: Stabilize Favilla

- Keep Favilla as the native capture shim.
- Show queued capture results instead of event ids.
- Add a safe “test token” path using empty text or `/api/health`, so testing does not wake AI.
- Add GitHub Actions APK build or a Gradle wrapper so Android changes are verifiable without local setup.

### Phase 1: PWA Companion

- Add mobile-friendly dashboard views: status, recent flow, pending queue, ai_state controls.
- Add manual beat cutting UI that works on phone: event boundary, topic drift boundary, DS proposal button.
- Add capture composer: text + quote + url + tags, posting to `/api/capture` as `source=app`.
- Add login/session handling around existing Caddy/Cloudflare auth.

### Phase 2: Native Android Shell

- Keep current PROCESS_TEXT and Share Sheet.
- Wrap the PWA in a native shell only for OS integrations: push notifications, quick capture tile, overlay bubble.
- Replace master ingest token with device token provisioning.
- Add local outbox retry queue for offline captures.

### Phase 3: Realtime Dispatch

- Add server-side app dispatch target: `fiam/dispatch/app` -> persistent app inbox.
- Add app inbox API plus ack/read receipts.
- Add push notifications for high-priority messages only; normal messages stay in app inbox.
- Respect `ai_state` and plugin enabled/disabled state consistently.

### Phase 4: Voice And Devices

- Voice call stays a separate plugin with stricter consent and logging rules.
- XIAO/Limen and ring publish derived signals, not raw continuous data by default.
- Health and biometric inputs should become short state beats, e.g. “sleep_quality=low” or “stress_signal=high”, unless raw data is explicitly needed.

## Immediate Code Tasks

- Add `EnvironmentFile=-/home/fiet/fiam-code/.env` to all services that depend on env secrets.
- Add a Gradle wrapper or CI workflow for `channels/favilla`.
- Extend `/api/capture` payload to preserve arbitrary `meta` safely.
- Add device-token model before shipping a general app build.
- Build the PWA mobile status/capture screen before adding more native UI.