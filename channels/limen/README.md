# Limen

Limen is the XIAO ESP32S3 wearable surface for Fiam. The current goal is modest on purpose: let the AI put messages, emoji, and kaomoji on the round screen reliably before camera/touch/audio are added.

## Hardware

- **XIAO ESP32S3 Sense** (pre-soldered pin header version)
- **Round Display for XIAO** (1.28" 240×240 touch)
- 3D printed case with pin clip

Current bench state:

- USB-C computer power is fine for firmware development.
- External antenna should be attached before mobile/hotspot field testing; WiFi stability is poor without it.
- The display coin cell is RTC backup, not required for screen polling.
- The 40300 LiPo should wait until polarity/connector/charging path are confirmed.
- Camera and touch are intentionally not connected in the active firmware path yet.

## What it does

Current screen-first build:

1. Connects to WiFi
2. Polls fiam `/api/wearable/reply`
3. Displays `message`, `kaomoji`, or `emoji` commands on the round screen

Camera, touch, STT, and TTS are deferred. Audio input should initially come through the phone/Bluetooth headset path, not the XIAO, because Android has stable mic permissions, codecs, network, and provider SDK options. XIAO audio can be revisited after screen/network are stable.

## Build

Requires [PlatformIO](https://platformio.org/).

```bash
cd channels/limen
pio run                  # compile
pio run -t upload        # flash via USB-C
pio device monitor       # serial log
```

## Config

Create `src/secrets.local.h` locally, or override these macros via build flags:
- WiFi SSID/password
- `FIAM_BASE_URL` (default `https://fiet.cc`)
- optional `FIAM_HOST_HEADER` for fixed-IP HTTPS fallbacks
- optional `FIAM_FIXED_IP` when device DNS cannot resolve the backend
- optional `FIAM_FIXED_IP_ALT` to rotate Cloudflare IPs on failures
- HTTP fixed-IP fallback can use `FIAM_BASE_URL` plus `FIAM_HOST_HEADER` without `FIAM_FIXED_IP`
- `FIAM_TOKEN` (same ingest token used by Favilla)

`src/secrets.local.h` is git-ignored.

## Network Strategy

Direct XIAO -> public `https://fiet.cc` polling works only intermittently on the current route; DNS/TLS through Cloudflare is fragile on ESP32S3 + hotspot/mobile paths. Prefer one of these paths:

1. XIAO talks to the phone on hotspot LAN; Favilla relays to Fiam over the phone's proxy/mobile route.
2. XIAO talks to a lightweight private broker/API reachable through Tailscale or another stable private network.
3. If the server moves, place the broker/API close to the phone's LA exit path and keep the ESP32 protocol simple.

For realtime screen updates, keep the device protocol tiny: short HTTP polling now, MQTT/WebSocket/UDP relay later if the phone bridge is added.

## Architecture

```
Fiet response ──[→xiao:screen]──► daemon/conductor ──MQTT──► dashboard queue
                                                                   │
Limen (XIAO) ◄────────────── WiFi GET /api/wearable/reply ◄────────┘
```

Screen command examples:

```text
[→xiao:screen] message:I'm here.
[→xiao:screen] kaomoji:(^-^)
[→xiao:screen] emoji:spark
```

Limen is a standalone client. It does not run Python or Fiam code. All intelligence lives on the server/phone side; the wearable displays compact commands and eventually contributes sensor context.

## Name

Latin *limen* = threshold, doorway. Internal device name only; app-facing naming is still intentionally undecided.
