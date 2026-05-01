#pragma once

// Display driver for Round Display (GC9A01 240x240)
// Uses Seeed_GFX/TFT_eSPI directly; touch/LVGL stay out of phase 1.

#include <TFT_eSPI.h>

#ifndef XIAO_BL
#define XIAO_BL D6
#endif
#define XIAO_DC D3
#define XIAO_CS D1

static TFT_eSPI tft = TFT_eSPI(240, 240);

uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

uint16_t colPaper() { return rgb565(250, 249, 245); }
uint16_t colInk() { return rgb565(31, 30, 27); }
uint16_t colMuted() { return rgb565(107, 104, 98); }
uint16_t colPeach() { return rgb565(217, 119, 87); }
uint16_t colSage() { return rgb565(122, 158, 142); }

void clearPaper() {
    tft.fillScreen(colPaper());
    tft.setTextColor(colInk());
    tft.setTextWrap(false, false);
}

void printWrapped(const String& text, int x, int y, int maxChars, int maxLines, uint8_t textSize) {
    tft.setTextSize(textSize);
    int line = 0;
    int pos = 0;
    while (pos < text.length() && line < maxLines) {
        String part = text.substring(pos, min(pos + maxChars, (int) text.length()));
        tft.setCursor(x, y + line * (8 * textSize + 6));
        tft.print(part);
        pos += maxChars;
        line++;
    }
}

void displayInit() {
    pinMode(XIAO_BL, OUTPUT);
    digitalWrite(XIAO_BL, HIGH);
    tft.begin();
    tft.setRotation(0);
    clearPaper();
    tft.setTextSize(2);
    Serial.println("[display] init OK");
}

void displayStatus(const char* line1, const char* line2 = nullptr) {
    clearPaper();
    tft.setTextColor(colPeach());
    tft.setTextSize(2);
    tft.setCursor(34, 88);
    tft.print(line1);
    if (line2) {
        tft.setTextColor(colMuted());
        tft.setTextSize(1);
        tft.setCursor(34, 122);
        tft.print(line2);
    }
}

void displayMessage(const String& text) {
    clearPaper();
    tft.setTextColor(colPeach());
    tft.setTextSize(1);
    tft.setCursor(44, 34);
    tft.print("fiam / stroll");
    tft.setTextColor(colInk());
    printWrapped(text.substring(0, 180), 30, 70, 18, 6, 2);
}

void displayKaomoji(const String& text) {
    clearPaper();
    tft.setTextColor(colInk());
    tft.setTextSize(3);
    tft.setCursor(32, 103);
    tft.print(text.substring(0, 14));
}

void drawSpark() {
    uint16_t c = colPeach();
    tft.fillCircle(120, 120, 10, c);
    tft.drawLine(120, 62, 120, 178, c);
    tft.drawLine(62, 120, 178, 120, c);
    tft.drawLine(82, 82, 158, 158, c);
    tft.drawLine(158, 82, 82, 158, c);
}

void drawHeart() {
    uint16_t c = colPeach();
    tft.fillCircle(98, 104, 26, c);
    tft.fillCircle(142, 104, 26, c);
    tft.fillTriangle(72, 118, 168, 118, 120, 178, c);
}

void drawSmile() {
    uint16_t c = colSage();
    tft.drawCircle(120, 120, 58, c);
    tft.fillCircle(100, 108, 5, colInk());
    tft.fillCircle(140, 108, 5, colInk());
    tft.drawLine(96, 138, 108, 148, colInk());
    tft.drawLine(108, 148, 132, 148, colInk());
    tft.drawLine(132, 148, 144, 138, colInk());
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
    tft.setTextColor(colMuted());
    tft.setTextSize(1);
    tft.setCursor(88, 198);
    tft.print(lower.substring(0, 12));
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
