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

// ---- Local device API ----
#ifndef LIMEN_DEVICE_ID
#define LIMEN_DEVICE_ID "limen-xiao"
#endif
#ifndef LIMEN_HTTP_PORT
#define LIMEN_HTTP_PORT 80
#endif

// ---- Hardware ----
#define SENSE_SD_CS_PIN 21   // Pull HIGH to disable Sense SD, free SPI for display
#define CAMERA_FRAME_SIZE FRAMESIZE_VGA  // 640x480, sharper phone preview

// ---- Behavior ----
#define WIFI_RETRY_INTERVAL_MS 10000
#define STREAM_FRAME_DELAY_MS 180
#define STREAM_MAX_MS 30000
#define DISPLAY_IDLE_OFF_MS 90000
#define TOUCH_RESET_WINDOW_MS 7000
#define TOUCH_RESET_TAPS 3
