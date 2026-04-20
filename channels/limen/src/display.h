#pragma once

// Display driver for Round Display (GC9A01 240x240)
// Uses Seeed_GFX library

#include "driver.h"  // Must define BOARD_SCREEN_COMBO=501

extern Arduino_GFX *gfx;

void displayInit() {
    gfx->begin();
    gfx->fillScreen(BLACK);
    gfx->setTextColor(WHITE);
    gfx->setTextSize(2);
    Serial.println("[display] init OK");
}

void displayStatus(const char* line1, const char* line2 = nullptr) {
    gfx->fillScreen(BLACK);
    gfx->setCursor(20, 100);
    gfx->print(line1);
    if (line2) {
        gfx->setCursor(20, 130);
        gfx->print(line2);
    }
}

void displayReply(const String& text) {
    gfx->fillScreen(BLACK);
    gfx->setTextSize(2);
    gfx->setTextWrap(true);
    gfx->setCursor(10, 30);

    // Simple word-wrap display on 240x240 round screen
    // Effective text area is roughly center 200x180
    gfx->print(text.substring(0, 200));  // Truncate for now
}

void displayEmoji(const char* emoji) {
    gfx->fillScreen(BLACK);
    gfx->setTextSize(4);
    gfx->setCursor(80, 90);
    gfx->print(emoji);
}
