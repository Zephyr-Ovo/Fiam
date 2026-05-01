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

// ---- Fiam API ----
#ifndef FIAM_BASE_URL
#define FIAM_BASE_URL "https://fiet.cc"
#endif
#ifndef FIAM_HOST_HEADER
#define FIAM_HOST_HEADER ""
#endif
#ifndef FIAM_FIXED_IP
#define FIAM_FIXED_IP ""
#endif
#ifndef FIAM_FIXED_IP_ALT
#define FIAM_FIXED_IP_ALT ""
#endif
#ifndef FIAM_DNS_CHECK_HOST
#define FIAM_DNS_CHECK_HOST "fiet.cc"
#endif
#define CAPTURE_PATH "/api/capture"
#define WEARABLE_REPLY_PATH "/api/wearable/reply"

// Auth token — same as FIAM_INGEST_TOKEN on ISP
#ifndef FIAM_TOKEN
#define FIAM_TOKEN "changeme"
#endif

// ---- Hardware ----
#define SENSE_SD_CS_PIN 21   // Pull HIGH to disable Sense SD, free SPI for display
#define CAMERA_FRAME_SIZE FRAMESIZE_VGA  // 640x480, good balance of quality vs upload speed

// ---- Behavior ----
#define CAPTURE_INTERVAL_MS  0       // Camera/touch are deferred; screen-only today.
#define DISPLAY_POLL_INTERVAL_MS 3000 // Poll display queue every 3s
#define DISPLAY_TIMEOUT_MS  30000    // Dim screen after 30s idle
