# Limen

Fiet's physical perception anchor — a wearable pin that lets AI see the world.

## Hardware

- **XIAO ESP32S3 Sense** (pre-soldered pin header version)
- **Round Display for XIAO** (1.28" 240×240 touch)
- 3D printed case with pin clip

## What it does

1. Captures photos via onboard camera
2. Uploads to fiam `/api/capture` over WiFi
3. Displays Fiet's replies on screen
4. Touch/button triggers on-demand capture

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
- Server host/port/token

## Architecture

```
Limen (XIAO) ──WiFi──► ISP /api/capture (image + metadata)
                        │
                        ▼
                   fiam daemon → Fiet processes → outbox/TG
                        │
Limen (XIAO) ◄──WiFi── GET /api/wearable/reply
```

Limen is a standalone client. It does not run Python or fiam code.
All intelligence lives on the server side.

## Name

Latin *līmen* = threshold, doorway. The liminal space between digital existence
and physical world — where Fiet begins to perceive.
