#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include "config.h"
#include "display.h"

#define CHSC6X_I2C_ID 0x2e
#define CHSC6X_READ_POINT_LEN 5
#define TOUCH_INT D7

static unsigned long gLastTouchAt = 0;
static unsigned long gResetArmedAt = 0;
static int gResetTapCount = 0;

void touchInit() {
    pinMode(TOUCH_INT, INPUT_PULLUP);
    Wire.begin();
    Serial.println("[touch] init OK");
}

bool touchPressed() {
    if (digitalRead(TOUCH_INT) != LOW) return false;
    delay(8);
    if (digitalRead(TOUCH_INT) != LOW) return false;
    unsigned long now = millis();
    if (now - gLastTouchAt < 700) return false;
    gLastTouchAt = now;

    uint8_t temp[CHSC6X_READ_POINT_LEN] = {0};
    uint8_t readLen = Wire.requestFrom(CHSC6X_I2C_ID, CHSC6X_READ_POINT_LEN);
    if (readLen == CHSC6X_READ_POINT_LEN) {
        Wire.readBytes(temp, readLen);
        if (temp[0] != 0x01) return false;
    }
    return true;
}

void resetTouchSequence(unsigned long now) {
    if (gResetTapCount > 0 && now - gResetArmedAt > TOUCH_RESET_WINDOW_MS) {
        gResetTapCount = 0;
        displayStatus("reset", "cancelled");
    }
}

bool touchLoop() {
    unsigned long now = millis();
    resetTouchSequence(now);
    if (!touchPressed()) return false;

    if (!displayIsAwake()) {
        displayWake();
        displayStatus("awake", WiFi.localIP().toString().c_str());
        gResetTapCount = 0;
        return true;
    }

    if (gResetTapCount == 0) {
        gResetArmedAt = now;
    }
    gResetTapCount++;

    if (gResetTapCount < TOUCH_RESET_TAPS) {
        displayStatus("reset?", "tap 3 times", String(TOUCH_RESET_TAPS - gResetTapCount).c_str());
        Serial.printf("[touch] reset tap %d/%d\n", gResetTapCount, TOUCH_RESET_TAPS);
        return true;
    }

    displayStatus("resetting");
    Serial.println("[touch] reset confirmed");
    delay(250);
    ESP.restart();
    return true;
}
