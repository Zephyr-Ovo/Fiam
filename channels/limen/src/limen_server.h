#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include <WebServer.h>
#include "config.h"
#include "camera.h"
#include "display.h"

static WebServer server(LIMEN_HTTP_PORT);

String jsonEscape(const String& s) {
    String out;
    for (size_t i = 0; i < s.length(); i++) {
        char c = s[i];
        if (c == '"' || c == '\\') {
            out += '\\';
            out += c;
        } else if (c == '\n') {
            out += "\\n";
        } else {
            out += c;
        }
    }
    return out;
}

void sendCors() {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.sendHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
    server.sendHeader("Access-Control-Allow-Headers", "Content-Type");
}

void handleOptions() {
    sendCors();
    server.send(204, "text/plain", "");
}

String deviceBaseUrl() {
    return "http://" + WiFi.localIP().toString();
}

void handleRoot() {
    sendCors();
    String html = "<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>";
    html += "<title>Limen</title>";
    html += "<body style='margin:0;background:#000;color:#fff;font:15px system-ui'>";
    html += "<main style='padding:18px'>";
    html += "<h1 style='font-size:20px'>Limen</h1>";
    html += "<p>Device: ";
    html += String(LIMEN_DEVICE_ID);
    html += "</p>";
    html += "<p>Preview auto-stops after 30 seconds to keep heat down.</p>";
    html += "<p><a style='display:inline-block;color:#000;background:#fff;padding:10px 14px;border-radius:8px;text-decoration:none' href='/stream'>Start preview</a></p>";
    html += "<p><a style='color:#fff' href='/capture'>Capture JPEG</a></p>";
    html += "<p><a style='color:#aaa' href='/health'>Health JSON</a></p>";
    html += "</main></body>";
    server.send(200, "text/html", html);
}

void handleHealth() {
    sendCors();
    String base = deviceBaseUrl();
    String body = "{";
    body += "\"ok\":true,";
    body += "\"device_id\":\"" + String(LIMEN_DEVICE_ID) + "\",";
    body += "\"role\":\"limen-camera\",";
    body += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
    body += "\"rssi\":" + String(WiFi.RSSI()) + ",";
    body += "\"capabilities\":[\"camera.snapshot\",\"camera.mjpeg\",\"screen.text\"],";
    body += "\"endpoints\":{";
    body += "\"health\":\"" + base + "/health\",";
    body += "\"stream\":\"" + base + "/stream\",";
    body += "\"capture\":\"" + base + "/capture\",";
    body += "\"screen\":\"" + base + "/screen\"";
    body += "}";
    body += "}";
    server.send(200, "application/json", body);
}

void handleCapture() {
    displayWake();
    camera_fb_t* fb = cameraCapture();
    if (!fb) {
        sendCors();
        server.send(503, "application/json", "{\"ok\":false,\"error\":\"capture_failed\"}");
        return;
    }

    sendCors();
    server.sendHeader("Cache-Control", "no-store");
    server.sendHeader("Content-Disposition", "inline; filename=\"limen.jpg\"");
    server.setContentLength(fb->len);
    server.send(200, "image/jpeg", "");
    WiFiClient client = server.client();
    client.write(fb->buf, fb->len);
    esp_camera_fb_return(fb);
    displayStatus("captured", WiFi.localIP().toString().c_str());
}

void handleStream() {
    displayWake();
    WiFiClient client = server.client();
    client.print(
        "HTTP/1.1 200 OK\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Cache-Control: no-cache\r\n"
        "Connection: close\r\n"
        "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n"
    );

    displayStatus("streaming", "auto stop 30s");
    unsigned long streamStartedAt = millis();
    while (client.connected() && millis() - streamStartedAt < STREAM_MAX_MS) {
        camera_fb_t* fb = cameraCapture();
        if (!fb) break;
        client.printf("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", (unsigned int)fb->len);
        client.write(fb->buf, fb->len);
        client.print("\r\n");
        esp_camera_fb_return(fb);
        delay(STREAM_FRAME_DELAY_MS);
    }
    client.print("--frame--\r\n");
    client.stop();
    displayNetwork(WiFi.localIP().toString());
}

void handleScreen() {
    String text = "";
    if (server.method() == HTTP_POST) {
        JsonDocument doc;
        DeserializationError err = deserializeJson(doc, server.arg("plain"));
        if (!err) {
            text = String((const char*) (doc["text"] | ""));
        }
    } else {
        text = server.arg("text");
    }

    text.trim();
    if (text.length() == 0) {
        sendCors();
        server.send(400, "application/json", "{\"ok\":false,\"error\":\"missing_text\"}");
        return;
    }
    displayWake();
    displayMessage(text);
    sendCors();
    server.send(200, "application/json", "{\"ok\":true,\"shown\":\"" + jsonEscape(text.substring(0, 180)) + "\"}");
}

void limenServerBegin() {
    server.on("/", HTTP_GET, handleRoot);
    server.on("/health", HTTP_GET, handleHealth);
    server.on("/capture", HTTP_GET, handleCapture);
    server.on("/stream", HTTP_GET, handleStream);
    server.on("/screen", HTTP_GET, handleScreen);
    server.on("/screen", HTTP_POST, handleScreen);
    server.onNotFound([]() {
        if (server.method() == HTTP_OPTIONS) {
            handleOptions();
            return;
        }
        sendCors();
        server.send(404, "application/json", "{\"ok\":false,\"error\":\"not_found\"}");
    });
    server.begin();
    Serial.printf("[http] listening on %s:%d\n", WiFi.localIP().toString().c_str(), LIMEN_HTTP_PORT);
}

void limenServerLoop() {
    server.handleClient();
}