#pragma once

#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include "config.h"
#include "esp_camera.h"

struct DisplayCommand {
    bool hasMessage = false;
    String type = "message";
    String text = "";
    int ttlMs = DISPLAY_TIMEOUT_MS;
};

bool beginHttp(HTTPClient& http, WiFiClientSecure& secure, const String& url) {
    if (url.startsWith("https://")) {
        secure.setInsecure();
        return http.begin(secure, url);
    }
    return http.begin(url);
}

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
    WiFiClientSecure secure;
    String url = String(FIAM_BASE_URL) + CAPTURE_PATH;
    if (!beginHttp(http, secure, url)) return -1;
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

// Poll next display command for the round screen.
DisplayCommand pollDisplayCommand() {
    DisplayCommand cmd;
    if (WiFi.status() != WL_CONNECTED) return cmd;

    HTTPClient http;
    WiFiClientSecure secure;
    String url = String(FIAM_BASE_URL) + WEARABLE_REPLY_PATH;
    if (!beginHttp(http, secure, url)) return cmd;
    http.addHeader("X-Fiam-Token", FIAM_TOKEN);

    int code = http.GET();
    String body = "";
    if (code == 200) {
        body = http.getString();
        JsonDocument doc;
        DeserializationError err = deserializeJson(doc, body);
        if (!err) {
            cmd.hasMessage = doc["has_message"] | false;
            cmd.type = String((const char*) (doc["type"] | "message"));
            cmd.text = String((const char*) (doc["text"] | ""));
            cmd.ttlMs = doc["ttl_ms"] | DISPLAY_TIMEOUT_MS;
        }
    }
    http.end();
    return cmd;
}
