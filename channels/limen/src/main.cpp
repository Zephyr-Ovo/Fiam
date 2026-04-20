// Limen — Fiet's physical perception anchor
// XIAO ESP32S3 Sense + Round Display for XIAO
//
// Phase 1: capture photo → POST to fiam /api/capture → display Fiet reply

#include <Arduino.h>
#include "config.h"
#include "camera.h"
#include "network.h"
#include "display.h"

static unsigned long lastCapture = 0;
static unsigned long lastPoll = 0;

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n=== Limen starting ===");

    // Disable Sense onboard SD card CS to free SPI for display
    pinMode(SENSE_SD_CS_PIN, OUTPUT);
    digitalWrite(SENSE_SD_CS_PIN, HIGH);

    // Init display
    displayInit();
    displayStatus("Limen", "starting...");

    // Init camera
    if (!cameraInit()) {
        displayStatus("Camera", "FAIL");
        while (1) delay(1000);
    }

    // Connect WiFi
    displayStatus("WiFi", "connecting...");
    if (!wifiConnect()) {
        displayStatus("WiFi", "FAIL");
        while (1) delay(1000);
    }

    displayStatus("Ready", WiFi.localIP().toString().c_str());
    delay(2000);
}

void loop() {
    unsigned long now = millis();

    // Auto-capture at interval (if enabled)
    if (CAPTURE_INTERVAL_MS > 0 && (now - lastCapture >= CAPTURE_INTERVAL_MS || lastCapture == 0)) {
        lastCapture = now;

        camera_fb_t* fb = cameraCapture();
        if (fb) {
            displayStatus("Uploading...");
            int code = uploadCapture(fb, "limen");
            esp_camera_fb_return(fb);

            if (code == 200 || code == 201) {
                displayStatus("Sent OK");
            } else {
                char buf[32];
                snprintf(buf, sizeof(buf), "HTTP %d", code);
                displayStatus("Upload err", buf);
            }
        }
    }

    // Poll for Fiet's reply
    if (now - lastPoll >= REPLY_POLL_INTERVAL_MS) {
        lastPoll = now;
        String reply = pollReply();
        if (reply.length() > 0) {
            displayReply(reply);
        }
    }

    delay(100);
}
