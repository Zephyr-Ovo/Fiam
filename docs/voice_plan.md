# Voice & Phone Plan

> 状态：**计划文档，未实施**。语音通道（STT/TTS）和电话呼叫（PSTN/WebRTC）的选型与架构。

## 0. 范围

- **入口**：Favilla 长按麦克风录音 + 未来 XIAO/Limen 麦克风
- **出口**：Favilla TTS 朗读 + 未来 XIAO 喇叭 + 真实电话回拨（远期）
- **不在本期**：实时语音通话（双工）、声纹识别、嘶声/情绪检测

## 1. STT（speech → text）

服务端 env：`FIAM_STT_API_KEY` / `FIAM_STT_BASE_URL`（已在 config 占位）

| 服务 | 价位 | 中英文 | 流式 | 备注 |
|------|------|--------|------|------|
| **Deepgram Nova-2/3** | ~$0.004/min | 优 | 是 | 最便宜+最快；推荐默认 |
| **OpenAI Whisper API** | $0.006/min | 优 | 否 | 离线模型（whisper.cpp）也可在 ISP 上跑 |
| **Google STT v2 (Chirp)** | $0.024/min | 优 | 是 | 中文方言识别强 |
| **Azure Speech** | $1/audio-hr | 优 | 是 | 适合企业账号已有的人 |
| **本地 whisper.cpp** | $0 | 中 | 是 | ISP 上 large-v3-turbo 4GB RAM 可跑；延迟 1-2x realtime |

**推荐**：
- 在线：Deepgram Nova（默认）
- 离线：whisper.cpp + `ggml-large-v3-turbo-q5_0.bin` 落到 ISP，作为 fallback

### 接入位置
- Favilla 端先压缩成 16k mono opus → POST `/api/stt`
- Server 调上游 → 返回 text → 走正常 `/api/capture` 进 flow

## 2. TTS（text → speech）

服务端 env：`FIAM_TTS_API_KEY` / `FIAM_TTS_BASE_URL`

| 服务 | 价位 | 中文 | 音色复刻 | 备注 |
|------|------|------|----------|------|
| **ElevenLabs** | ~$0.18/1k chars | 优 | 是（Pro） | 表情张力最强 |
| **Cartesia (Sonic)** | ~$0.10/1k chars | 优 | 是 | 延迟低，适合 stroll 模式 |
| **Fish Audio** | ~$0.05/1k chars | 优（中文母语团队） | 是 | 国内可达 |
| **OpenAI TTS** | $0.015/1k chars | 优 | 否 | gpt-4o-mini-tts 表情可控 |
| **Azure Neural TTS** | $4/1M chars | 优 | 是 | 大量预设中文音色 |
| **edge-tts (free)** | 免费 | 优 | 否 | Edge 浏览器 TTS API；非官方但稳定 |
| **piper (本地)** | $0 | 中 | 否 | CPU 实时；先做 demo 用 |

**推荐**：
- 主：Cartesia（性价比 + 延迟）
- 中文表达力优先：Fish Audio
- 免费占位：edge-tts via `edge-tts` Python 包

### 接入位置
- Server 在 outbox dispatch 时，对 `[→favilla:Zephyr]` 消息可选生成 TTS audio attach
- Favilla "stroll mode" 直接 stream TTS chunks 到 ExoPlayer

## 3. 电话（PSTN）

呼出/呼入到中国 +86 号码，需要：

### 路径 A：国际 SIP trunk
- **Twilio** — https://twilio.com — 全球覆盖，**对中国 +86 出价较高（~$0.10/min）**，号码租用需备案
- **Telnyx** — https://telnyx.com — 价格便宜，PSTN 质量看路由
- **Plivo** — https://plivo.com — 类似 Twilio 但更便宜
- **Vonage** — https://vonage.com — 同类

**注意**：
- 中国电信运营商对 SIP 入境有限制，呼入 +86 号码可能拒接或质量差
- 呼出到 +86 → 通常可达，但显示的主叫号码可能被 OTT 或运营商替换

### 路径 B：国内运营商 API
- **腾讯云 IVR / 阿里云通信** — 实名认证 + 企业资质要求，个人难以申请
- **环信 / 容联云通讯** — 同上

### 路径 C：WebRTC（绕过 PSTN）
- **LiveKit** — https://livekit.io — 自托管 SFU；Favilla 内嵌 WebRTC client
- **Daily.co** — https://daily.co — SaaS WebRTC，免费 quota 1k min/月
- **Agora** — 国内可达，10k min/月免费
- 不打 PSTN，纯 app-to-AI 实时语音；用户体验类似"对讲机"

### 推荐分阶段
1. **先做 WebRTC** (LiveKit 自托管 on ISP)：Favilla ↔ Claude 实时语音对话；零电信牌照
2. **再做 PSTN 出方向**：Twilio 拨号到指定号码（提醒/通知场景）；不接受呼入
3. **PSTN 入方向放最后或永不**：备案/合规成本太高

## 4. 协议设计（实时语音对话）

```
Favilla ──WebRTC audio──► LiveKit (ISP) ──opus chunks──► Server
                                                              ↓
                                                       STT (streaming)
                                                              ↓
                                                       partial text → Claude (CC)
                                                              ↓
                                                       reply text → TTS streaming
                                                              ↓
Favilla ◄──WebRTC audio──── LiveKit ◄──opus chunks──── Server
```

- VAD（voice activity detect）由 Favilla 端做，silence > 800ms → flush turn
- Claude 走 `--output-format stream-json` 模式，token 流即时给 TTS
- Barge-in（用户打断）：检测到新 voice → cancel 当前 TTS

## 5. 当前 Favilla 占位

`strings.xml` 已有：
- `more_section_voice` "Voice channels (STT & TTS)"
- `more_stt_url` / `more_stt_key` / `more_tts_url` / `more_tts_key`
- `hub_section_voice` "Voice note" + "Hold to record" hint

**实施时**：补 `RecordButton` + `MediaRecorder` (opus/16k) + `OkHttp` 上传 + `ExoPlayer` 播放即可。

## 6. 不做（先记下）

- 声纹/说话人识别
- 嘈杂环境降噪（先依赖手机硬件 echo cancel）
- 多语言 code-switch 自动检测（先在 Settings 里手选 zh/en）
- 唤醒词（"嘿 Claude"）— 改用 Favilla 长按或 XIAO 实体按钮
