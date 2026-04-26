// Limen — Fiet's physical perception anchor
// XIAO ESP32S3 Sense + Round Display for XIAO
//
// Phase 1 screen-only: poll fiam display queue → render message / kaomoji / emoji

#include <Arduino.h>
#include "config.h"
#include "network.h"
#include "display.h"

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

    // Connect WiFi
    displayStatus("WiFi", "connecting...");
    if (!wifiConnect()) {
        displayStatus("WiFi", "FAIL");
        while (1) delay(1000);
    }

    displayStatus("Screen ready", WiFi.localIP().toString().c_str());
    delay(2000);
}

void loop() {
    unsigned long now = millis();

    if (now - lastPoll >= DISPLAY_POLL_INTERVAL_MS) {
        lastPoll = now;
        DisplayCommand cmd = pollDisplayCommand();
        if (cmd.hasMessage && cmd.text.length() > 0) {
            displayCommand(cmd);
        }
    }

    delay(100);
}
