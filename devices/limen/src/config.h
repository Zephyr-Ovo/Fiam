#pragma once

// ---- Network ----
// WiFi credentials (override via build flags or serial config later)
#ifndef WIFI_SSID
#define WIFI_SSID "your-ssid"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS "your-pass"
#endif

// ---- Fiam API ----
#define CAPTURE_HOST "fiet.cc"
#define CAPTURE_PORT 8766
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
#define CAPTURE_INTERVAL_MS  300000  // Auto-capture every 5 min (0 = manual only)
#define REPLY_POLL_INTERVAL_MS 15000 // Poll for Fiet reply every 15s
#define DISPLAY_TIMEOUT_MS  30000    // Dim screen after 30s idle
