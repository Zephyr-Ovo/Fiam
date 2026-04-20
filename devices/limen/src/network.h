#pragma once

#include <WiFi.h>
#include <HTTPClient.h>
#include "config.h"
#include "esp_camera.h"

bool wifiConnect() {
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.print("[wifi] connecting");
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 40) {
        delay(500);
        Serial.print(".");
        attempts++;
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("\n[wifi] connected, IP: %s\n", WiFi.localIP().toString().c_str());
        return true;
    }
    Serial.println("\n[wifi] FAILED");
    return false;
}

// Upload JPEG frame to /api/capture as multipart/form-data
// Returns HTTP status code, or -1 on error
int uploadCapture(camera_fb_t* fb, const char* source) {
    if (WiFi.status() != WL_CONNECTED) return -1;
    if (!fb || !fb->buf) return -1;

    HTTPClient http;
    String url = String("http://") + CAPTURE_HOST + ":" + CAPTURE_PORT + CAPTURE_PATH;
    http.begin(url);
    http.addHeader("X-Fiam-Token", FIAM_TOKEN);

    // Build multipart body
    String boundary = "----LimenBoundary";
    http.addHeader("Content-Type", "multipart/form-data; boundary=" + boundary);

    // Assemble parts: source field + image file
    String head = "--" + boundary + "\r\n"
                  "Content-Disposition: form-data; name=\"source\"\r\n\r\n"
                  + String(source) + "\r\n"
                  "--" + boundary + "\r\n"
                  "Content-Disposition: form-data; name=\"image\"; filename=\"capture.jpg\"\r\n"
                  "Content-Type: image/jpeg\r\n\r\n";
    String tail = "\r\n--" + boundary + "--\r\n";

    size_t totalLen = head.length() + fb->len + tail.length();

    // Stream upload
    uint8_t* body = (uint8_t*)ps_malloc(totalLen);
    if (!body) {
        Serial.println("[upload] malloc failed");
        http.end();
        return -1;
    }
    memcpy(body, head.c_str(), head.length());
    memcpy(body + head.length(), fb->buf, fb->len);
    memcpy(body + head.length() + fb->len, tail.c_str(), tail.length());

    int code = http.POST(body, totalLen);
    free(body);

    Serial.printf("[upload] HTTP %d\n", code);
    http.end();
    return code;
}

// Poll Fiet's reply for display on screen
// Returns reply text, empty string if none
String pollReply() {
    if (WiFi.status() != WL_CONNECTED) return "";

    HTTPClient http;
    String url = String("http://") + CAPTURE_HOST + ":" + CAPTURE_PORT + WEARABLE_REPLY_PATH;
    http.begin(url);
    http.addHeader("X-Fiam-Token", FIAM_TOKEN);

    int code = http.GET();
    String result = "";
    if (code == 200) {
        result = http.getString();
    }
    http.end();
    return result;
}
