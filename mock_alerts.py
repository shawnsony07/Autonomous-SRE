import paho.mqtt.client as mqtt
import json
import time
import random

# MQTT Broker Settings
BROKER = "localhost"
PORT = 1883
TELEMETRY_TOPIC = "sre/edge/telemetry"

# Simulated Edge Device Anomalies
anomalies = [
    {
        "device_id": "esp32-node-telematics",
        "timestamp": 0, # Will be set dynamically
        "status": "critical",
        "metrics": {
            "cpu_temp": 86.5,
            "ram_usage": "65%",
            "network_latency_ms": 12
        },
        "error_code": "ERR_THERMAL_RUNAWAY",
        "message": "Critical temperature threshold exceeded. Hardware damage imminent."
    },
    {
        "device_id": "esp32-node-telematics",
        "timestamp": 0,
        "status": "warning",
        "metrics": {
            "cpu_temp": 45.0,
            "ram_usage": "98%",
            "network_latency_ms": 150
        },
        "error_code": "WARN_MEM_LEAK",
        "message": "High heap memory usage detected. Potential buffer overflow in sensor stream."
    }
]

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[+] Connected to local Mosquitto Broker successfully.")
    else:
        print(f"[-] Failed to connect. Return code: {rc}")

def main():
    # Initialize MQTT Client
    client = mqtt.Client()
    client.on_connect = on_connect

    print(f"Connecting to broker at {BROKER}:{PORT}...")
    client.connect(BROKER, PORT, 60)
    client.loop_start()

    try:
        # Select a random anomaly to simulate
        payload = random.choice(anomalies)
        payload["timestamp"] = int(time.time())
        
        print(f"\n[!] Publishing Mock Telemetry to '{TELEMETRY_TOPIC}':")
        print(json.dumps(payload, indent=2))
        
        # Publish the payload
        client.publish(TELEMETRY_TOPIC, json.dumps(payload))
        
        print("\n[+] Payload sent! Monitor your sre_agent logs to watch the AI respond.")
        time.sleep(2) # Allow network transmission before exit
        
    except KeyboardInterrupt:
        print("\nExiting simulation.")
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
