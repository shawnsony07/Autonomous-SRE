#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <WiFiClientSecure.h> // Required for TLS/SSL

// ==========================================
// Network Configuration
// ==========================================
const char* ssid        = "YOUR_WIFI_SSID";
const char* password    = "YOUR_WIFI_PASSWORD";
const char* mqtt_server = "YOUR_MQTT_SERVER_IP"; 
const int   mqtt_port   = 8883; // 8883 is the encrypted MQTTS port

// Topics matching src/main.py and src/tools.py
const char* telemetry_topic = "sre/edge/telemetry";
const char* command_topic   = "sre/edge/commands";

WiFiClientSecure espClient; // Use the Secure client
PubSubClient client(espClient);

unsigned long lastTelemetryTime = 0;

// ==========================================
// MQTT Callback (Handling Agent Commands)
// ==========================================
void callback(char* topic, byte* payload, unsigned int length) {
  String command = "";
  for (int i = 0; i < length; i++) {
    command += (char)payload[i];
  }
  command.trim();

  Serial.println("\n----------------------------------");
  Serial.print("[!] AGENT REMEDIATION RECEIVED: ");
  Serial.println(command);
  Serial.println("----------------------------------");

  // Execute received remediation action
  if (command == "FAN_ON") {
    Serial.println("--> [ACTION]: Turning ON cooling fan relay/GPIO...");
  } else if (command == "THROTTLE_CPU") {
    Serial.println("--> [ACTION]: Throttling ESP32 CPU frequency to 80MHz...");
    setCpuFrequencyMhz(80);
  } else if (command == "RESET_I2C") {
    Serial.println("--> [ACTION]: Toggling I2C bus power pin to re-initialize sensors...");
  } else if (command == "DEEP_SLEEP") {
    Serial.println("--> [ACTION]: Entering low-power deep sleep mode...");
  } else {
    Serial.print("--> [ACTION]: Executed custom edge command: ");
    Serial.println(command);
  }
}

// ==========================================
// Network Helper Functions
// ==========================================
void setup_wifi() {
  delay(10);
  Serial.println();
  Serial.print("Connecting to Wi-Fi: ");
  Serial.println(ssid);

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nWi-Fi Connected!");
  Serial.print("IP Address: ");
  Serial.println(WiFi.localIP());

  // CRITICAL: Tells the ESP32 to encrypt the traffic but skip 
  // strict Certificate/Hostname validation. Perfect for EC2 IPs!
  espClient.setInsecure();
}

void reconnect() {
  while (!client.connected()) {
    Serial.print("Connecting to MQTT Broker at ");
    Serial.print(mqtt_server);
    Serial.print("...");

    String clientId = "XIAO-ESP32S3-";
    clientId += String(random(0xffff), HEX);

    if (client.connect(clientId.c_str())) {
      Serial.println(" Connected Securely!");
      // Subscribe to remediation topic from src/tools.py
      client.subscribe(command_topic);
      Serial.print("Subscribed to: ");
      Serial.println(command_topic);
    } else {
      Serial.print(" Failed, rc=");
      Serial.print(client.state());
      Serial.println(". Retrying in 5 seconds...");
      delay(5000);
    }
  }
}

// ==========================================
// Helper: Send Anomaly Telemetry
// ==========================================
void sendAnomalyPayload(const char* alert_type, const char* raw_logs) {
  StaticJsonDocument<256> doc;
  doc["id"] = "xiao-esp32s3-node-01";
  doc["alert_type"] = alert_type;
  doc["raw_logs"] = raw_logs;

  char buffer[256];
  serializeJson(doc, buffer);

  Serial.println("\n[!] Publishing Anomaly Telemetry to SRE Agent:");
  Serial.println(buffer);
  client.publish(telemetry_topic, buffer);
}

// ==========================================
// Arduino Setup & Loop
// ==========================================
void setup() {
  Serial.begin(115200);
  setup_wifi();
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  // Send periodic heartbeat / anomaly every 30 seconds for testing
  unsigned long now = millis();
  if (now - lastTelemetryTime > 30000) {
    lastTelemetryTime = now;

    // Send mock hardware log matching your database vector memory
    sendAnomalyPayload(
      "Thermal Runaway", 
      "ESP32 thermal sensor reports temperature exceeding 85C for 60 seconds."
    );
  }
}