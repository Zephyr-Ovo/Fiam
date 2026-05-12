// Limen — Claude's physical perception anchor
// XIAO ESP32S3 Sense + Round Display for XIAO
//
// MQTT screen peripheral: daemon pushes display/cmd messages; touch reports back.

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include "config.h"
#include "display.h"
#include "touch.h"

static unsigned long lastWifiAttempt = 0;
static unsigned long lastMqttAttempt = 0;
static WiFiClient mqttNet;
static PubSubClient mqtt(mqttNet);

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

void publishMqttStatus(const char* status) {
    if (!mqtt.connected()) return;
    String payload = "{\"device_id\":\"";
    payload += LIMEN_DEVICE_ID;
    payload += "\",\"status\":\"";
    payload += status;
    payload += "\",\"ip\":\"";
    payload += WiFi.localIP().toString();
    payload += "\",\"rssi\":";
    payload += String(WiFi.RSSI());
    payload += "}";
    mqtt.publish(LIMEN_MQTT_STATUS_TOPIC, payload.c_str());
}

void handleMqttMessage(char* topic, byte* payload, unsigned int length) {
    String body;
    body.reserve(length + 1);
    for (unsigned int i = 0; i < length; i++) {
        body += static_cast<char>(payload[i]);
    }
    String topicName(topic);
    Serial.printf("[mqtt] %s %u bytes\n", topic, length);
    if (topicName == LIMEN_MQTT_DISPLAY_TOPIC) {
        displayMessage(body);
    } else if (topicName == LIMEN_MQTT_CMD_TOPIC) {
        body.trim();
        body.toLowerCase();
        if (body == "reset" || body == "restart") {
            displayStatus("resetting", "mqtt");
            delay(250);
            ESP.restart();
        } else if (body == "status") {
            publishMqttStatus("ok");
            displayNetwork(WiFi.localIP().toString());
        } else {
            displayStatus("cmd", body.c_str());
        }
    }
}

bool mqttConnect(unsigned long now) {
    if (mqtt.connected()) return true;
    if (now - lastMqttAttempt < MQTT_RETRY_INTERVAL_MS) return false;
    lastMqttAttempt = now;
    String clientId = String(LIMEN_DEVICE_ID) + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
    Serial.printf("[mqtt] connecting to %s:%d\n", LIMEN_MQTT_HOST, LIMEN_MQTT_PORT);
    if (!mqtt.connect(clientId.c_str())) {
        Serial.printf("[mqtt] failed rc=%d\n", mqtt.state());
        return false;
    }
    mqtt.subscribe(LIMEN_MQTT_DISPLAY_TOPIC, 1);
    mqtt.subscribe(LIMEN_MQTT_CMD_TOPIC, 1);
    publishMqttStatus("online");
    Serial.println("[mqtt] connected");
    return true;
}

void publishTouch() {
    if (!mqtt.connected()) return;
    String payload = "{\"device_id\":\"";
    payload += LIMEN_DEVICE_ID;
    payload += "\",\"event\":\"touch\",\"t\":";
    payload += String(millis());
    payload += "}";
    mqtt.publish(LIMEN_MQTT_TOUCH_TOPIC, payload.c_str());
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n=== Limen MQTT display starting ===");

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

    mqtt.setServer(LIMEN_MQTT_HOST, LIMEN_MQTT_PORT);
    mqtt.setCallback(handleMqttMessage);
    mqttConnect(millis());
    displayNetwork(WiFi.localIP().toString());
}

void loop() {
    unsigned long now = millis();

    if (!ensureWifi(now)) {
        displayTick(now);
        delay(100);
        return;
    }

    mqttConnect(now);
    mqtt.loop();
    if (touchLoop()) {
        publishTouch();
    }
    displayTick(now);
    delay(2);
}
