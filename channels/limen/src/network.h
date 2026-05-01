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

static int wearableFailureCount = 0;
static bool useAltFixedIp = false;

bool fixedIpPollingEnabled() {
    return String(FIAM_FIXED_IP).length() > 0 && String(FIAM_HOST_HEADER).length() > 0;
}

String currentFixedIp() {
    String alt = String(FIAM_FIXED_IP_ALT);
    if (useAltFixedIp && alt.length() > 0) return alt;
    return String(FIAM_FIXED_IP);
}

void noteWearableFailure() {
    wearableFailureCount++;
    if (String(FIAM_FIXED_IP_ALT).length() > 0) {
        useAltFixedIp = !useAltFixedIp;
    }
    if (wearableFailureCount >= 4) {
        Serial.println("[wifi] reconnecting after wearable failures");
        WiFi.disconnect(false);
        delay(500);
        WiFi.begin(WIFI_SSID, WIFI_PASS);
        wearableFailureCount = 0;
    }
}

void noteWearableSuccess() {
    wearableFailureCount = 0;
}

int parseHttpStatus(const String& statusLine) {
    int firstSpace = statusLine.indexOf(' ');
    if (firstSpace < 0 || statusLine.length() < firstSpace + 4) return -1;
    return statusLine.substring(firstSpace + 1, firstSpace + 4).toInt();
}

String readFixedIpBody(Client& client, int contentLength, bool chunked) {
    String body = "";
    unsigned long lastDataAt = millis();
    if (chunked) {
        while (client.connected() || client.available()) {
            if (!client.available() && millis() - lastDataAt > 2500) break;
            String sizeLine = client.readStringUntil('\n');
            sizeLine.trim();
            if (sizeLine.length() == 0) continue;
            int chunkSize = strtol(sizeLine.c_str(), nullptr, 16);
            if (chunkSize <= 0) break;
            while (chunkSize > 0 && (client.connected() || client.available())) {
                if (!client.available()) {
                    if (millis() - lastDataAt > 2500) break;
                    delay(10);
                    continue;
                }
                body += char(client.read());
                lastDataAt = millis();
                chunkSize--;
            }
            client.readStringUntil('\n');
        }
        return body;
    }

    if (contentLength >= 0) {
        while (contentLength > 0 && (client.connected() || client.available())) {
            if (!client.available()) {
                if (millis() - lastDataAt > 2500) break;
                delay(10);
                continue;
            }
            body += char(client.read());
            lastDataAt = millis();
            contentLength--;
        }
        return body;
    }

    while (client.connected() || client.available()) {
        if (!client.available()) {
            if (millis() - lastDataAt > 2500) break;
            delay(10);
            continue;
        }
        body += char(client.read());
        lastDataAt = millis();
    }
    return body;
}

void writeFixedIpRequest(Client& client, const char* path) {
    client.print("GET ");
    client.print(path);
    client.print(" HTTP/1.0\r\nHost: ");
    client.print(FIAM_HOST_HEADER);
    client.print("\r\nX-Fiam-Token: ");
    client.print(FIAM_TOKEN);
    client.print("\r\nUser-Agent: limen/0.1\r\nConnection: close\r\n\r\n");
}

int readFixedIpResponse(Client& client, String& body) {
    String statusLine = client.readStringUntil('\n');
    statusLine.trim();
    int code = parseHttpStatus(statusLine);
    if (code != 200) {
        Serial.printf("[wearable] status: %s\n", statusLine.c_str());
    }

    int contentLength = -1;
    bool chunked = false;
    while (client.connected() || client.available()) {
        String header = client.readStringUntil('\n');
        header.trim();
        if (header.length() == 0) break;
        String lower = header;
        lower.toLowerCase();
        if (lower.startsWith("content-length:")) {
            contentLength = header.substring(15).toInt();
        } else if (lower.startsWith("transfer-encoding:") && lower.indexOf("chunked") >= 0) {
            chunked = true;
        }
    }

    if (code != 200) {
        client.stop();
        body = "";
        return code;
    }

    body = readFixedIpBody(client, contentLength, chunked);
    client.stop();
    return code;
}

int fixedIpGet(const char* path, String& body) {
    IPAddress ip;
    String ipText = currentFixedIp();
    if (!ip.fromString(ipText)) {
        Serial.println("[wearable] bad fixed IP");
        return -1;
    }
    Serial.printf("[wearable] GET %s via %s\n", path, ipText.c_str());

    if (!String(FIAM_BASE_URL).startsWith("https://")) {
        WiFiClient client;
        client.setTimeout(5000);
        if (!client.connect(ip, 80, 5000)) {
            Serial.printf("[wearable] fixed-IP HTTP connect failed: %s\n", ipText.c_str());
            return -1;
        }
        writeFixedIpRequest(client, path);
        return readFixedIpResponse(client, body);
    }

    WiFiClientSecure secure;
    secure.setInsecure();
    secure.setTimeout(15000);
    secure.setHandshakeTimeout(30);
    if (!secure.connect(ip, 443, FIAM_HOST_HEADER, nullptr, nullptr, nullptr)) {
        Serial.printf("[wearable] fixed-IP TLS connect failed: %s\n", ipText.c_str());
        return -1;
    }

    writeFixedIpRequest(secure, path);
    return readFixedIpResponse(secure, body);
}

bool beginHttp(HTTPClient& http, WiFiClientSecure& secure, const String& url) {
    bool ok = false;
    if (url.startsWith("https://")) {
        secure.setInsecure();
        secure.setHandshakeTimeout(30);
        ok = http.begin(secure, url);
    } else {
        ok = http.begin(url);
    }
    if (ok) {
        http.setReuse(false);
        http.setTimeout(15000);
    }
    return ok;
}

bool wifiConnect() {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE, IPAddress(1, 1, 1, 1), IPAddress(8, 8, 8, 8));
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
        String dnsCheckHost = String(FIAM_DNS_CHECK_HOST);
        if (dnsCheckHost.length() > 0) {
            IPAddress apiIp;
            if (WiFi.hostByName(dnsCheckHost.c_str(), apiIp)) {
                Serial.printf("[wifi] %s: %s\n", dnsCheckHost.c_str(), apiIp.toString().c_str());
            } else {
                Serial.printf("[wifi] DNS lookup failed for %s\n", dnsCheckHost.c_str());
            }
        }
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
    String hostHeader = String(FIAM_HOST_HEADER);
    if (hostHeader.length() > 0) http.addHeader("Host", hostHeader);
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
    String body = "";
    int code = 0;
    if (fixedIpPollingEnabled()) {
        code = fixedIpGet(WEARABLE_REPLY_PATH, body);
    } else {
        String url = String(FIAM_BASE_URL) + WEARABLE_REPLY_PATH;
        if (!beginHttp(http, secure, url)) return cmd;
        String hostHeader = String(FIAM_HOST_HEADER);
        if (hostHeader.length() > 0) http.addHeader("Host", hostHeader);
        http.addHeader("X-Fiam-Token", FIAM_TOKEN);
        code = http.GET();
        if (code == 200) body = http.getString();
    }

    if (code == 200) {
        noteWearableSuccess();
        JsonDocument doc;
        DeserializationError err = deserializeJson(doc, body);
        if (!err) {
            cmd.hasMessage = doc["has_message"] | false;
            cmd.type = String((const char*) (doc["type"] | "message"));
            cmd.text = String((const char*) (doc["text"] | ""));
            cmd.ttlMs = doc["ttl_ms"] | DISPLAY_TIMEOUT_MS;
            Serial.printf("[wearable] HTTP 200 message=%d\n", cmd.hasMessage ? 1 : 0);
        } else {
            Serial.println("[wearable] JSON parse failed");
        }
    } else if (code < 0) {
        noteWearableFailure();
        Serial.printf("[wearable] GET failed: %s\n", http.errorToString(code).c_str());
    } else {
        noteWearableFailure();
        Serial.printf("[wearable] HTTP %d\n", code);
    }
    if (!fixedIpPollingEnabled()) http.end();
    return cmd;
}
