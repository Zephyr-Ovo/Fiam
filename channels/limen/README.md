# Limen

Fiet's physical perception anchor — a wearable pin that gives AI a tiny physical surface.

## Hardware

- **XIAO ESP32S3 Sense** (pre-soldered pin header version)
- **Round Display for XIAO** (1.28" 240×240 touch)
- 3D printed case with pin clip

## What it does

Current screen-first build:

1. Connects to WiFi
2. Polls fiam `/api/wearable/reply`
3. Displays `message`, `kaomoji`, or `emoji` commands on the round screen

Camera and touch are present in hardware but intentionally deferred.

## Build

Requires [PlatformIO](https://platformio.org/).

```bash
cd channels/limen
pio run                  # compile
pio run -t upload        # flash via USB-C
pio device monitor       # serial log
```

## Config

Edit `src/config.h`:
- WiFi SSID/password
- `FIAM_BASE_URL` (default `https://fiet.cc`)
- `FIAM_TOKEN` (same ingest token used by Favilla)

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

Limen is a standalone client. It does not run Python or fiam code.
All intelligence lives on the server side.

## Name

Latin *līmen* = threshold, doorway. The liminal space between digital existence
and physical world — where Fiet begins to perceive.
