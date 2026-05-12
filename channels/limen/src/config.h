#pragma once

#if __has_include("secrets.local.h")
#include "secrets.local.h"
#endif

// ---- Network ----
// WiFi credentials (override via build flags or serial config later)
#ifndef WIFI_SSID
#define WIFI_SSID "your-ssid"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS "your-pass"
#endif

// ---- MQTT device API ----
#ifndef LIMEN_DEVICE_ID
#define LIMEN_DEVICE_ID "limen-xiao"
#endif
#ifndef LIMEN_MQTT_HOST
#define LIMEN_MQTT_HOST "127.0.0.1"
#endif
#ifndef LIMEN_MQTT_PORT
#define LIMEN_MQTT_PORT 1883
#endif
#ifndef LIMEN_MQTT_DISPLAY_TOPIC
#define LIMEN_MQTT_DISPLAY_TOPIC "limen/display"
#endif
#ifndef LIMEN_MQTT_CMD_TOPIC
#define LIMEN_MQTT_CMD_TOPIC "limen/cmd"
#endif
#ifndef LIMEN_MQTT_TOUCH_TOPIC
#define LIMEN_MQTT_TOUCH_TOPIC "limen/touch"
#endif
#ifndef LIMEN_MQTT_STATUS_TOPIC
#define LIMEN_MQTT_STATUS_TOPIC "limen/status"
#endif

// ---- Hardware ----
#define SENSE_SD_CS_PIN 21   // Pull HIGH to disable Sense SD, free SPI for display
#define CAMERA_FRAME_SIZE FRAMESIZE_VGA  // 640x480, sharper phone preview

// ---- Behavior ----
#define WIFI_RETRY_INTERVAL_MS 10000
#define MQTT_RETRY_INTERVAL_MS 5000
#define STREAM_FRAME_DELAY_MS 180
#define STREAM_MAX_MS 30000
#define DISPLAY_IDLE_OFF_MS 90000
#define TOUCH_RESET_WINDOW_MS 7000
#define TOUCH_RESET_TAPS 3
