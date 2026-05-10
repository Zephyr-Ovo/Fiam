// Limen — Claude's physical perception anchor
// XIAO ESP32S3 Sense + Round Display for XIAO
//
// Local camera/screen peripheral: Favilla can view /stream, capture /capture,
// and mirror AI text to /screen.

#include <Arduino.h>
#include <WiFi.h>
#include "config.h"
#include "display.h"
#include "camera.h"
#include "limen_server.h"
#include "touch.h"

static unsigned long lastWifiAttempt = 0;

bool wifiConnect() {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.print("[wifi] connecting");

    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 40) {
        delay(500);
        Serial.print(".");
        attempts++;
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[wifi] connected, IP: %s RSSI=%d\n",
            WiFi.localIP().toString().c_str(),
            WiFi.RSSI()
        );
        return true;
    }

    Serial.println("\n[wifi] FAILED");
    return false;
}

bool ensureWifi(unsigned long now) {
    if (WiFi.status() == WL_CONNECTED) return true;
    if (now - lastWifiAttempt < WIFI_RETRY_INTERVAL_MS) return false;
    lastWifiAttempt = now;
    displayStatus("WiFi", "retrying");
    return wifiConnect();
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n=== Limen camera starting ===");

    // Disable Sense onboard SD card CS to free SPI for the round display.
    pinMode(SENSE_SD_CS_PIN, OUTPUT);
    digitalWrite(SENSE_SD_CS_PIN, HIGH);

    displayInit();
    touchInit();
    displayStatus("limen", "starting");

    displayStatus("WiFi", "connecting");
    if (!wifiConnect()) {
        displayStatus("WiFi", "failed", "check hotspot");
        lastWifiAttempt = millis();
        return;
    }

    displayStatus("camera", "starting");
    if (!cameraInit()) {
        displayStatus("camera", "failed");
        return;
    }

    limenServerBegin();
    displayNetwork(WiFi.localIP().toString());
}

void loop() {
    unsigned long now = millis();

    if (!ensureWifi(now)) {
        displayTick(now);
        delay(100);
        return;
    }

    limenServerLoop();
    touchLoop();
    displayTick(now);
    delay(2);
}
