#pragma once

#include <Arduino.h>
#include <TFT_eSPI.h>
#include <math.h>
#include "config.h"

#ifndef XIAO_BL
#define XIAO_BL D6
#endif

static TFT_eSPI tft = TFT_eSPI(240, 240);
static const int SCREEN_CENTER = 120;
static const int SAFE_RADIUS = 88;
static unsigned long gDisplayLastActiveAt = 0;
static bool gDisplayAwake = true;

uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b) {
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}

uint16_t colBg() { return TFT_BLACK; }
uint16_t colText() { return TFT_WHITE; }
uint16_t colDim() { return rgb565(150, 150, 150); }
uint16_t colPeach() { return rgb565(244, 166, 142); }
uint16_t colSage() { return rgb565(136, 175, 175); }
uint16_t colBlue() { return rgb565(120, 150, 205); }

bool displayIsAwake() {
    return gDisplayAwake;
}

void displayWake() {
    digitalWrite(XIAO_BL, HIGH);
    gDisplayAwake = true;
    gDisplayLastActiveAt = millis();
}

void displaySleep() {
    digitalWrite(XIAO_BL, LOW);
    gDisplayAwake = false;
}

void displayTick(unsigned long now) {
    if (gDisplayAwake && now - gDisplayLastActiveAt > DISPLAY_IDLE_OFF_MS) {
        displaySleep();
    }
}

void clearScreen() {
    displayWake();
    tft.fillScreen(colBg());
    tft.setTextColor(colText(), colBg());
    tft.setTextWrap(false, false);
}

uint16_t colorForName(String name) {
    name.toLowerCase();
    if (name == "dim") return colDim();
    if (name == "peach") return colPeach();
    if (name == "sage") return colSage();
    if (name == "blue") return colBlue();
    return colText();
}

String takePrefix(String& text, const String& prefix) {
    if (!text.startsWith(prefix)) return "";
    text = text.substring(prefix.length());
    int sep = text.indexOf(':');
    if (sep < 0) return "";
    String value = text.substring(0, sep);
    text = text.substring(sep + 1);
    value.trim();
    return value;
}

String stripMode(String& text) {
    String modes[] = { "word:", "kaomoji:", "face:", "emoji:", "anim:", "cal:", "status:", "message:" };
    for (int i = 0; i < 8; i++) {
        if (text.startsWith(modes[i])) {
            String mode = modes[i].substring(0, modes[i].length() - 1);
            text = text.substring(modes[i].length());
            text.trim();
            return mode;
        }
    }
    return "message";
}

int chordWidthAtY(int y) {
    int dy = abs(y - SCREEN_CENTER);
    if (dy >= SAFE_RADIUS) return 0;
    int half = sqrt((SAFE_RADIUS * SAFE_RADIUS) - (dy * dy));
    return max(0, (half * 2) - 12);
}

int chordLeftAtY(int y) {
    int width = chordWidthAtY(y);
    return SCREEN_CENTER - (width / 2);
}

void drawCenteredLine(const String& line, int y, uint8_t textSize, uint16_t color) {
    tft.setTextSize(textSize);
    tft.setTextColor(color, colBg());
    int width = tft.textWidth(line);
    int x = SCREEN_CENTER - width / 2;
    tft.setCursor(max(8, x), y);
    tft.print(line);
}

void drawSymbol(String name, uint16_t color);

void drawThickLine(int x0, int y0, int x1, int y1, uint16_t color, int thickness = 3) {
    int radius = thickness / 2;
    for (int dx = -radius; dx <= radius; dx++) {
        for (int dy = -radius; dy <= radius; dy++) {
            if (dx * dx + dy * dy <= radius * radius) {
                tft.drawLine(x0 + dx, y0 + dy, x1 + dx, y1 + dy, color);
            }
        }
    }
}

void drawThickCircle(int x, int y, int radius, uint16_t color, int thickness = 4) {
    for (int offset = 0; offset < thickness; offset++) {
        tft.drawCircle(x, y, radius - offset, color);
    }
}

bool drawSpecialFace(String text, uint16_t color) {
    text.replace(" ", "");
    text.toLowerCase();
    if (text != "ov<" && text != "0v<") return false;
    drawThickCircle(72, 112, 30, color, 5);
    drawThickLine(180, 84, 146, 114, color, 5);
    drawThickLine(146, 114, 180, 144, color, 5);
    drawThickLine(104, 140, 120, 160, color, 4);
    drawThickLine(120, 160, 136, 140, color, 4);
    return true;
}

void drawFace(String name, uint16_t color) {
    if (drawSpecialFace(name, color)) return;
    drawSymbol(name, color);
}

void drawWrappedMessage(String text, uint16_t color) {
    tft.setTextSize(2);
    tft.setTextColor(color, colBg());
    const int lineHeight = 23;
    const int maxLines = 5;
    int y = 58;
    int line = 0;
    text.trim();
    while (text.length() > 0 && line < maxLines) {
        int centerY = y + 8;
        int maxWidth = chordWidthAtY(centerY);
        int best = 0;
        int lastSpace = -1;
        for (int i = 1; i <= text.length(); i++) {
            if (text.charAt(i - 1) == ' ') lastSpace = i - 1;
            String candidate = text.substring(0, i);
            if (tft.textWidth(candidate) > maxWidth) break;
            best = i;
        }
        if (best <= 0) best = 1;
        if (lastSpace > 0 && best < text.length()) best = lastSpace;
        String part = text.substring(0, best);
        part.trim();
        int x = chordLeftAtY(centerY);
        tft.setCursor(x, y);
        tft.print(part);
        text = text.substring(best);
        text.trim();
        y += lineHeight;
        line++;
    }
}

void drawWord(String text, uint16_t color) {
    text.trim();
    uint8_t size = text.length() <= 7 ? 4 : 3;
    drawCenteredLine(text.substring(0, 18), size == 4 ? 101 : 104, size, color);
}

void drawKaomoji(String text, uint16_t color) {
    text.trim();
    if (drawSpecialFace(text, color)) return;
    uint8_t size = text.length() <= 3 ? 8 : text.length() <= 5 ? 6 : text.length() <= 8 ? 4 : 2;
    int y = size == 8 ? 90 : size == 6 ? 92 : size == 4 ? 101 : 110;
    drawCenteredLine(text.substring(0, 18), y, size, color);
}

void drawSymbol(String name, uint16_t color) {
    name.toLowerCase();
    if (name.indexOf("heart") >= 0) {
        tft.fillCircle(98, 104, 22, color);
        tft.fillCircle(142, 104, 22, color);
        tft.fillTriangle(76, 116, 164, 116, 120, 174, color);
    } else if (name.indexOf("smile") >= 0) {
        tft.drawCircle(120, 120, 58, color);
        tft.fillCircle(100, 108, 5, color);
        tft.fillCircle(140, 108, 5, color);
        tft.drawLine(96, 138, 108, 148, color);
        tft.drawLine(108, 148, 132, 148, color);
        tft.drawLine(132, 148, 144, 138, color);
    } else {
        tft.fillCircle(120, 120, 9, color);
        tft.drawLine(120, 62, 120, 178, color);
        tft.drawLine(62, 120, 178, 120, color);
        tft.drawLine(82, 82, 158, 158, color);
        tft.drawLine(158, 82, 82, 158, color);
    }
}

void drawAnim(String name, uint16_t color) {
    name.toLowerCase();
    for (int r = 18; r <= 58; r += 10) {
        clearScreen();
        tft.drawCircle(120, 120, r, color);
        tft.fillCircle(120, 120, 6, color);
        delay(90);
    }
}

void displayInit() {
    pinMode(XIAO_BL, OUTPUT);
    digitalWrite(XIAO_BL, HIGH);
    gDisplayAwake = true;
    gDisplayLastActiveAt = millis();
    tft.begin();
    tft.setRotation(0);
    clearScreen();
    Serial.println("[display] init OK");
}

void displayStatus(const char* line1, const char* line2 = nullptr, const char* line3 = nullptr) {
    clearScreen();
    drawWord(String(line1), colText());
    if (line2) drawCenteredLine(String(line2).substring(0, 24), 154, 1, colDim());
    if (line3) drawCenteredLine(String(line3).substring(0, 24), 170, 1, colDim());
}

void displayMessage(const String& rawText) {
    String text = rawText.substring(0, 180);
    text.trim();
    uint16_t color = colText();
    if (text.startsWith("color:")) {
        String colorName = takePrefix(text, "color:");
        color = colorForName(colorName);
    }
    String mode = stripMode(text);
    clearScreen();
    if (mode == "word" || mode == "status") drawWord(text, color);
    else if (mode == "kaomoji") drawKaomoji(text, color);
    else if (mode == "face") drawFace(text, color);
    else if (mode == "emoji") drawSymbol(text, color);
    else if (mode == "anim") drawAnim(text, color);
    else drawWrappedMessage(text, color);
}

void displayNetwork(const String& ip) {
    clearScreen();
    drawWord("ready", colText());
    drawCenteredLine(ip, 154, 1, colDim());
}
