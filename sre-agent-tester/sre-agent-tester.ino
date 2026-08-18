#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <WiFiClientSecure.h> 
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// ==========================================
// OLED Display Configuration
// ==========================================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1 
#define SCREEN_ADDRESS 0x3C // 0x3C is standard for 0.96" OLEDs
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// Display State Machine Variables
enum ScreenState { NORMAL, ANIMATING_FAN, SHOW_MSG };
ScreenState currentScreenState = NORMAL;
String screenMessage = "";
unsigned long animationStart = 0;
unsigned long lastFrameTime = 0;
int fanFrame = 0;

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

WiFiClientSecure espClient; 
PubSubClient client(espClient);

unsigned long lastTelemetryTime = 0;

// ==========================================
// Display Helper Functions
// ==========================================
void showMessage(String msg, int durationMs = 3000) {
  currentScreenState = SHOW_MSG;
  screenMessage = msg;
  animationStart = millis();
}

void triggerFanAnimation() {
  currentScreenState = ANIMATING_FAN;
  animationStart = millis();
}

void drawFanFrame(int frame) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 5);
  display.println("AGENT ACTION:");
  display.println("FAN_ON received");
  display.println("Cooling system active");

  int cx = 64; // Center X
  int cy = 42; // Center Y
  int r = 12;  // Radius

  // Draw Fan Housing
  display.drawCircle(cx, cy, r + 4, SSD1306_WHITE);

  // Simple spinning fan logic
  if (frame == 0) {
    display.drawLine(cx, cy - r, cx, cy + r, SSD1306_WHITE);
    display.drawLine(cx - r, cy, cx + r, cy, SSD1306_WHITE);
  } else {
    // 45-degree angle lines
    display.drawLine(cx - r + 3, cy - r + 3, cx + r - 3, cy + r - 3, SSD1306_WHITE);
    display.drawLine(cx + r - 3, cy - r + 3, cx - r + 3, cy + r - 3, SSD1306_WHITE);
  }
  display.display();
}

void updateDisplay() {
  // Throttle updates to ~10Hz (100ms) to prevent screen flicker and CPU hogging
  if (millis() - lastFrameTime < 100) return;
  lastFrameTime = millis();

  if (currentScreenState == ANIMATING_FAN) {
    if (millis() - animationStart > 5000) { // Run animation for 5 seconds
      currentScreenState = NORMAL;
    } else {
      drawFanFrame(fanFrame);
      fanFrame = (fanFrame + 1) % 2; // Toggle between frame 0 and 1
    }
  } 
  else if (currentScreenState == SHOW_MSG) {
    if (millis() - animationStart > 4000) { // Show message for 4 seconds
      currentScreenState = NORMAL;
    } else {
      display.clearDisplay();
      display.setTextSize(1);
      display.setTextColor(SSD1306_WHITE);
      display.setCursor(0, 10);
      display.println(">> SYSTEM OVERRIDE:");
      display.println("");
      display.println(screenMessage);
      display.display();
    }
  } 
  else { // NORMAL STATE
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.setCursor(0,0);
    display.println(" EdgeOps SRE Agent");
    display.println("---------------------");
    display.print("WiFi: ");
    display.println(WiFi.status() == WL_CONNECTED ? "OK" : "ERR");
    display.print("MQTT: ");
    display.println(client.connected() ? "SECURE SYNC" : "DISCONNECTED");
    display.println("");
    
    // Countdown to next telemetry
    int nextPing = 30 - ((millis() - lastTelemetryTime) / 1000);
    display.print("Next telemetry: ");
    display.print(nextPing > 0 ? nextPing : 0);
    display.println("s");
    display.display();
  }
}

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

  // Execute received remediation action and update OLED
  if (command == "FAN_ON") {
    Serial.println("--> [ACTION]: Turning ON cooling fan relay/GPIO...");
    triggerFanAnimation();
  } 
  else if (command == "THROTTLE_CPU") {
    Serial.println("--> [ACTION]: Throttling ESP32 CPU frequency to 80MHz...");
    showMessage("Throttling CPU\nfreq to 80MHz...");
    setCpuFrequencyMhz(80);
  } 
  else if (command == "RESET_I2C") {
    Serial.println("--> [ACTION]: Toggling I2C bus power pin to re-initialize sensors...");
    showMessage("Hard Resetting\nI2C Sensor Bus...");
  } 
  else if (command == "DEEP_SLEEP") {
    Serial.println("--> [ACTION]: Entering low-power deep sleep mode...");
    showMessage("ENTERING\nDEEP SLEEP MODE", 10000);
  } 
  else {
    Serial.print("--> [ACTION]: Executed custom edge command: ");
    Serial.println(command);
    showMessage("CMD Executed:\n" + command);
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
  // strict Certificate/Hostname validation.
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
  
  // Flash the screen briefly to show it sent
  showMessage("Anomaly Published:\n" + String(alert_type), 2000);
}

// ==========================================
// Arduino Setup & Loop
// ==========================================
void setup() {
  Serial.begin(115200);

  // Initialize OLED
  if(!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println(F("SSD1306 allocation failed"));
    for(;;); // Don't proceed, loop forever
  }
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(10, 25);
  display.println("Booting SRE Node...");
  display.display();

  setup_wifi();
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
}

void loop() {
  if (!client.connected()) {
    reconnect();
  }
  client.loop();

  // Run display state machine (Non-blocking)
  updateDisplay();

  // Send periodic anomaly every 30 seconds for testing
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