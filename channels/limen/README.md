# Limen

Limen is the XIAO ESP32S3 wearable surface for Fiam. The current target is a local Stroll camera/screen peripheral: Favilla can view the live camera, capture a still photo, and let the AI mirror short text onto the round display.

## Hardware

- **XIAO ESP32S3 Sense** (pre-soldered pin header version)
- **Round Display for XIAO** (1.28" 240×240 touch)
- 3D printed case with pin clip

Current bench state:

- USB-C computer power is fine for firmware development.
- External antenna should be attached before mobile/hotspot field testing; WiFi stability is poor without it.
- The display coin cell is RTC backup, not required for screen polling.
- The 40300 LiPo should wait until polarity/connector/charging path are confirmed.
- Camera and touch are active in the local HTTP firmware path.

## What it does

Current local camera build:

1. Connects to WiFi
2. Serves `GET /health`, `GET /stream`, `GET /capture`, and `POST /screen`
3. Displays AI text, emoji/face tokens, status, and capture/stream state on the round screen
4. Sleeps the display backlight after idle; network and camera stay available for Favilla actions

Audio input should initially come through the phone/Bluetooth headset path, not the XIAO, because Android has stable mic permissions, codecs, network, and provider SDK options. XIAO audio can be revisited after screen/network are stable.

## Build

Requires [PlatformIO](https://platformio.org/).

```bash
cd channels/limen
pio run                  # compile
pio run -t upload        # flash via USB-C
pio device monitor       # serial log
```

## Config

Set these environment variables before building:

- `LIMEN_WIFI_SSID`
- `LIMEN_WIFI_PASS`
- optional `LIMEN_DEVICE_ID` via build flag if needed

Runtime keys/tokens stay in local/server environment variables. The XIAO local camera firmware does not embed `FIAM_INGEST_TOKEN`.

## Network Strategy

Direct XIAO -> public `https://fiet.cc` polling is no longer the first target for Stroll. Prefer one of these paths:

1. XIAO talks to the phone on hotspot LAN; Favilla relays to Fiam over the phone's proxy/mobile route.
2. XIAO talks to a lightweight private broker/API reachable through Tailscale or another stable private network.
3. If the server moves, place the broker/API close to the phone's LA exit path and keep the ESP32 protocol simple.

For realtime screen updates, keep the device protocol tiny: local HTTP now, MQTT/WebSocket/UDP relay later if the phone bridge is added.

## Architecture

```
AI hidden Stroll action ──► Favilla Stroll ──► http://<xiao-ip>/screen|capture|stream
```

Local API:

```text
GET  http://<xiao-ip>/health
GET  http://<xiao-ip>/stream
GET  http://<xiao-ip>/capture
POST http://<xiao-ip>/screen  {"text":"ready"}
```

Touch reset is deliberately hard to trigger: tap the round display three times within seven seconds. A sleeping display wakes on the first touch and does not count that touch as reset intent.

Limen is a standalone client. It does not run Python or Fiam code. All intelligence lives on the server/phone side; the wearable displays compact commands and contributes camera context.

## Name

Latin *limen* = threshold, doorway. Internal device name only; app-facing naming is still intentionally undecided.
