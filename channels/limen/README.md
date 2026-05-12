# Limen

Limen is the XIAO ESP32S3 wearable screen surface for Fiam. The current firmware is a pure MQTT display client: the server pushes compact display/cmd messages, and Limen publishes touch/status events back.

## Hardware

- XIAO ESP32S3 Sense
- Round Display for XIAO, 1.28" 240x240 touch
- 3D printed case with pin clip

## What it does

1. Connects to WiFi.
2. Connects to the MQTT broker.
3. Subscribes to `limen/display` and `limen/cmd`.
4. Renders short words, messages, kaomoji, faces, symbols, and status.
5. Publishes touch events to `limen/touch`.
6. Publishes online/status reports to `limen/status`.

There is no HTTP server and no camera path in this firmware.

## Build

Requires PlatformIO.

```bash
cd channels/limen
pio run
pio run -t upload
pio device monitor
```

## Config

Set these environment variables before building:

- `LIMEN_WIFI_SSID`
- `LIMEN_WIFI_PASS`

Optional build flags:

- `LIMEN_DEVICE_ID`
- `LIMEN_MQTT_HOST`
- `LIMEN_MQTT_PORT`
- `LIMEN_MQTT_DISPLAY_TOPIC`
- `LIMEN_MQTT_CMD_TOPIC`
- `LIMEN_MQTT_TOUCH_TOPIC`
- `LIMEN_MQTT_STATUS_TOPIC`

## MQTT

Default topics:

```text
limen/display  <- text payload to render
limen/cmd      <- status | reset | restart
limen/touch    -> {"device_id":"limen-xiao","event":"touch","t":...}
limen/status   -> {"device_id":"limen-xiao","status":"online","ip":"...","rssi":...}
```

Limen is a standalone client. It does not run Python or Fiam code.
