// Limen — Claude's physical perception anchor
// XIAO ESP32S3 Sense + Round Display for XIAO
//
// Phase 1 screen-only: poll fiam display queue → render message / kaomoji / emoji

#include <Arduino.h>
#include "config.h"
#include "network.h"
#include "display.h"

static unsigned long lastPoll = 0;
static unsigned long lastWifiAttempt = 0;

bool ensureWifi(unsigned long now) {
    if (WiFi.status() == WL_CONNECTED) return true;
    if (now - lastWifiAttempt < 10000) return false;
    lastWifiAttempt = now;
    displayStatus("WiFi", "retrying...");
    return wifiConnect();
}

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

    // Connect WiFi
    displayStatus("WiFi", "connecting...");
    if (!wifiConnect()) {
        displayStatus("WiFi", "FAIL");
        lastWifiAttempt = millis();
        return;
    }

    displayStatus("Screen ready", WiFi.localIP().toString().c_str());
    delay(2000);
}

void loop() {
    unsigned long now = millis();

    if (!ensureWifi(now)) {
        delay(250);
        return;
    }

    if (now - lastPoll >= DISPLAY_POLL_INTERVAL_MS) {
        lastPoll = now;
        DisplayCommand cmd = pollDisplayCommand();
        if (cmd.hasMessage && cmd.text.length() > 0) {
            Serial.printf("[display] %s: %s\n", cmd.type.c_str(), cmd.text.c_str());
            displayCommand(cmd);
            Serial.println("[display] done");
        }
    }

    delay(100);
}
