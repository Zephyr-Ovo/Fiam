#pragma once

// Display driver for Round Display (GC9A01 240x240)
// Uses Seeed_GFX library

#include "driver.h"  // Must define BOARD_SCREEN_COMBO=501

extern Arduino_GFX *gfx;

uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

uint16_t colPaper() { return rgb565(250, 249, 245); }
uint16_t colInk() { return rgb565(31, 30, 27); }
uint16_t colMuted() { return rgb565(107, 104, 98); }
uint16_t colPeach() { return rgb565(217, 119, 87); }
uint16_t colSage() { return rgb565(122, 158, 142); }

void clearPaper() {
    gfx->fillScreen(colPaper());
    gfx->setTextColor(colInk());
    gfx->setTextWrap(false);
}

void printWrapped(const String& text, int x, int y, int maxChars, int maxLines, uint8_t textSize) {
    gfx->setTextSize(textSize);
    int line = 0;
    int pos = 0;
    while (pos < text.length() && line < maxLines) {
        String part = text.substring(pos, min(pos + maxChars, (int) text.length()));
        gfx->setCursor(x, y + line * (8 * textSize + 6));
        gfx->print(part);
        pos += maxChars;
        line++;
    }
}

void displayInit() {
    gfx->begin();
    clearPaper();
    gfx->setTextSize(2);
    Serial.println("[display] init OK");
}

void displayStatus(const char* line1, const char* line2 = nullptr) {
    clearPaper();
    gfx->setTextColor(colPeach());
    gfx->setTextSize(2);
    gfx->setCursor(34, 88);
    gfx->print(line1);
    if (line2) {
        gfx->setTextColor(colMuted());
        gfx->setTextSize(1);
        gfx->setCursor(34, 122);
        gfx->print(line2);
    }
}

void displayMessage(const String& text) {
    clearPaper();
    gfx->setTextColor(colPeach());
    gfx->setTextSize(1);
    gfx->setCursor(44, 34);
    gfx->print("fiam / stroll");
    gfx->setTextColor(colInk());
    printWrapped(text.substring(0, 180), 30, 70, 18, 6, 2);
}

void displayKaomoji(const String& text) {
    clearPaper();
    gfx->setTextColor(colInk());
    gfx->setTextSize(3);
    gfx->setCursor(32, 103);
    gfx->print(text.substring(0, 14));
}

void drawSpark() {
    uint16_t c = colPeach();
    gfx->fillCircle(120, 120, 10, c);
    gfx->drawLine(120, 62, 120, 178, c);
    gfx->drawLine(62, 120, 178, 120, c);
    gfx->drawLine(82, 82, 158, 158, c);
    gfx->drawLine(158, 82, 82, 158, c);
}

void drawHeart() {
    uint16_t c = colPeach();
    gfx->fillCircle(98, 104, 26, c);
    gfx->fillCircle(142, 104, 26, c);
    gfx->fillTriangle(72, 118, 168, 118, 120, 178, c);
}

void drawSmile() {
    uint16_t c = colSage();
    gfx->drawCircle(120, 120, 58, c);
    gfx->fillCircle(100, 108, 5, colInk());
    gfx->fillCircle(140, 108, 5, colInk());
    gfx->drawLine(96, 138, 108, 148, colInk());
    gfx->drawLine(108, 148, 132, 148, colInk());
    gfx->drawLine(132, 148, 144, 138, colInk());
}

void displayEmoji(const String& text) {
    clearPaper();
    String lower = text;
    lower.toLowerCase();
    if (lower.indexOf("heart") >= 0 || lower.indexOf("love") >= 0) {
        drawHeart();
    } else if (lower.indexOf("smile") >= 0 || lower.indexOf("happy") >= 0) {
        drawSmile();
    } else {
        drawSpark();
    }
    gfx->setTextColor(colMuted());
    gfx->setTextSize(1);
    gfx->setCursor(88, 198);
    gfx->print(lower.substring(0, 12));
}

void displayCommand(const DisplayCommand& cmd) {
    if (cmd.type == "emoji") {
        displayEmoji(cmd.text);
    } else if (cmd.type == "kaomoji") {
        displayKaomoji(cmd.text);
    } else if (cmd.type == "status") {
        displayStatus(cmd.text.c_str());
    } else {
        displayMessage(cmd.text);
    }
}
