"""
scripts/mock_alerts.py
─────────────────────
Manual / automated test harness for the Autonomous SRE Agent.

Injects synthetic incident payloads directly into the Mosquitto MQTTS broker
on sre/edge/telemetry, bypassing the physical ESP32 hardware.  The agent's
on_message handler treats these payloads identically to live ESP32 telemetry.

Usage
─────
  # Interactive menu (pick a single scenario)
  python scripts/mock_alerts.py

  # Fire one specific scenario by name
  python scripts/mock_alerts.py --scenario thermal_runaway

  # Fire every scenario sequentially (full regression)
  python scripts/mock_alerts.py --all

  # Stress test: repeat a scenario N times with a delay
  python scripts/mock_alerts.py --scenario voltage_drop --repeat 5 --delay 10

  # Use a custom broker / cert path
  python scripts/mock_alerts.py --host 1.2.3.4 --port 8883 --cafile certs/ca.crt

Environment variables (all optional, fall back to sensible defaults)
─────────────────────────────────────────────────────────────────────
  MQTT_BROKER_HOST   Broker hostname / IP  (default: localhost)
  MQTT_BROKER_PORT   Broker port           (default: 8883)
  MQTT_CA_CERT       Path to CA cert file  (default: certs/ca.crt)
"""

import argparse
import json
import os
import ssl
import sys
import time
import uuid

import paho.mqtt.client as mqtt

# ──────────────────────────────────────────────────────────────────────────────
# MQTT configuration
# ──────────────────────────────────────────────────────────────────────────────
TELEMETRY_TOPIC = "sre/edge/telemetry"
DEFAULT_HOST    = os.getenv("MQTT_BROKER_HOST", "localhost")
DEFAULT_PORT    = int(os.getenv("MQTT_BROKER_PORT", "8883"))
DEFAULT_CA      = os.getenv("MQTT_CA_CERT", "certs/ca.crt")

# ──────────────────────────────────────────────────────────────────────────────
# Scenario catalogue
# ─────────────────────────────────────────────────────────────────────────────
# Each entry maps to the exact payload shape expected by src/main.py::on_message
# and src/graph.py::AgentState:
#   id         → incident_id / thread_id (string, unique per run)
#   alert_type → pre-classified anomaly label for Detect_Ingest node
#   raw_logs   → raw sensor text ingested by the LLM
#
# write_consent is intentionally included on destructive scenarios so the
# HITL gate triggers — this lets you validate the full Slack + terminal
# approval flow without touching real infrastructure.
# ──────────────────────────────────────────────────────────────────────────────
SCENARIOS: dict[str, dict] = {

    # ── Non-destructive: auto-resolved, no HITL ─────────────────────────────

    "thermal_runaway": {
        "description": "Thermal Runaway — CPU temperature critical (auto-resolved: FAN_ON)",
        "alert_type":  "Thermal Runaway",
        "raw_logs":    (
            "ESP32 thermal sensor reports temperature exceeding 85C for 60 seconds. "
            "Core 0 load: 97%. Thermal throttle not yet engaged. Fan PWM at 0%."
        ),
    },

    "sensor_disconnect": {
        "description": "Sensor Disconnect — I2C bus timeout (auto-resolved: RESET_I2C)",
        "alert_type":  "Sensor Disconnect",
        "raw_logs":    (
            "I2C bus timeout reading BMP280 pressure sensor at address 0x76. "
            "SCL held low for >500ms. Wire.endTransmission() returned error code 4. "
            "Three consecutive read failures recorded."
        ),
    },

    "voltage_drop": {
        "description": "Voltage Drop — VCC rail unstable (auto-resolved: DEEP_SLEEP)",
        "alert_type":  "Voltage Drop",
        "raw_logs":    (
            "ADC reading on VCC rail: 2.87V — below 2.9V threshold. "
            "Brown-out detector fired once at 14:23:07 UTC. "
            "Peripheral draw: BLE radio + SD card active simultaneously."
        ),
    },

    "memory_exhaustion": {
        "description": "Memory Exhaustion — heap fragmentation critical (auto-resolved)",
        "alert_type":  "Memory Exhaustion",
        "raw_logs":    (
            "Free heap: 3,412 bytes (14% of total). "
            "Largest contiguous free block: 512 bytes. "
            "ArduinoJson::DynamicJsonDocument allocation failed. "
            "System has been running for 47 hours without reboot."
        ),
    },

    "watchdog_reset": {
        "description": "Watchdog Reset — task starvation detected (auto-resolved)",
        "alert_type":  "Watchdog Reset",
        "raw_logs":    (
            "Task watchdog triggered on core 1. Offending task: mqtt_loop_task. "
            "Stack high-water mark: 128 bytes. Guru Meditation Error: Core 1 "
            "panic'ed (Task watchdog got triggered.). Reset reason: RTCWDT_RTC_RESET."
        ),
    },

    "wifi_reconnect_loop": {
        "description": "Wi-Fi Reconnect Loop — broker unreachable (auto-resolved)",
        "alert_type":  "Network Instability",
        "raw_logs":    (
            "WiFi.status() returned WL_CONNECTION_LOST 7 times in 90 seconds. "
            "RSSI: -88 dBm. MQTT broker at 98.130.53.27:8883 unreachable. "
            "PubSubClient::connect() returned false. Reconnect backoff: 5s."
        ),
    },

    # ── Destructive: triggers HITL gate (write_consent = True) ──────────────

    "db_table_corruption": {
        "description": "DB Table Corruption — triggers HITL gate for DROP TABLE approval",
        "alert_type":  "Database Table Corruption",
        "raw_logs":    (
            "CockroachDB reports checksum mismatch on table old_metrics. "
            "Queries against old_metrics returning 'pq: invalid page in block 42'. "
            "Table size: 2.1 GB. Last vacuumed: never. Recommend dropping and rebuilding."
        ),
        "write_consent": True,
    },

    "disk_threshold_breach": {
        "description": "Disk Threshold Breach — triggers HITL gate for log rotation approval",
        "alert_type":  "Disk Threshold Breach",
        "raw_logs":    (
            "EC2 instance /var/log partition at 97% capacity (38.8 GB / 40 GB). "
            "Docker overlay2 consuming 22 GB. sre_agent container log: 8.1 GB. "
            "Write IOPS throttled. Recommend log rotation and container prune."
        ),
        "write_consent": True,
    },

    "runaway_process": {
        "description": "Runaway Process — triggers HITL gate for process kill approval",
        "alert_type":  "Runaway Process",
        "raw_logs":    (
            "Process 'ollama serve' consuming 98% CPU for 25 minutes. "
            "Memory RSS: 14.2 GB / 15.5 GB available. OOM killer invoked twice. "
            "PID: 4821. Recommend SIGKILL if CPU does not drop below 80% within 60s."
        ),
        "write_consent": True,
    },

    # ── Cold-start (no historical match expected) ────────────────────────────

    "unknown_anomaly": {
        "description": "Unknown Anomaly — cold-start, no historical match, tests DLQ path",
        "alert_type":  "Unknown Hardware Anomaly",
        "raw_logs":    (
            "Unrecognised interrupt 0xFF fired 312 times in 10 seconds on GPIO 34. "
            "No registered ISR handler. Signal toggling at 50kHz. "
            "Suspected RF interference or faulty pull-up resistor."
        ),
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# MQTT publish helpers
# ──────────────────────────────────────────────────────────────────────────────

def _build_client(host: str, port: int, ca_cert: str) -> mqtt.Client:
    """Create a connected, TLS-secured MQTT client."""
    client_id = f"mock-alert-injector-{uuid.uuid4().hex[:8]}"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)

    if not os.path.exists(ca_cert):
        print(f"[WARN] CA cert not found at '{ca_cert}'. Connecting with setInsecure().")
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)
    else:
        client.tls_set(ca_certs=ca_cert)

    connected = {"rc": None}

    def on_connect(c, userdata, flags, reason_code, properties):
        connected["rc"] = reason_code

    client.on_connect = on_connect
    client.connect(host, port, keepalive=10)
    client.loop_start()

    # Wait up to 5 s for the broker to acknowledge connection
    deadline = time.time() + 5
    while connected["rc"] is None and time.time() < deadline:
        time.sleep(0.05)

    if connected["rc"] is None:
        client.loop_stop()
        raise TimeoutError(f"Broker at {host}:{port} did not respond within 5s.")

    return client


def _publish(client: mqtt.Client, scenario_name: str, scenario: dict) -> None:
    """Build and publish a single telemetry payload."""
    payload = {
        "id":         f"mock-{scenario_name}-{uuid.uuid4().hex[:6]}",
        "alert_type": scenario["alert_type"],
        "raw_logs":   scenario["raw_logs"],
    }
    # Carry write_consent through if the scenario declares it
    if scenario.get("write_consent"):
        payload["write_consent"] = True

    raw = json.dumps(payload)
    result = client.publish(TELEMETRY_TOPIC, raw, qos=1)
    result.wait_for_publish(timeout=5)

    print(f"  ✅  Published  [{scenario['alert_type']}]")
    print(f"      id       : {payload['id']}")
    print(f"      topic    : {TELEMETRY_TOPIC}")
    if payload.get("write_consent"):
        print(f"      ⚠️  HITL gate will trigger — approval required within 120s")
    print()


# ──────────────────────────────────────────────────────────────────────────────
# Interactive menu
# ──────────────────────────────────────────────────────────────────────────────

def _print_banner():
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║        Autonomous SRE Agent — Mock Alert Injector        ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()


def _print_menu():
    print("Available scenarios:")
    print()
    for i, (key, val) in enumerate(SCENARIOS.items(), 1):
        tag = " ⚠️  HITL" if val.get("write_consent") else "       "
        print(f"  [{i:2d}]{tag}  {key}")
        print(f"         {val['description']}")
    print()
    print("  [ 0]         Run ALL scenarios sequentially")
    print()


def _interactive_menu(client: mqtt.Client) -> None:
    _print_menu()
    keys = list(SCENARIOS.keys())

    while True:
        try:
            raw = input("Enter scenario number (or 'q' to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return

        if raw.lower() == "q":
            return

        try:
            choice = int(raw)
        except ValueError:
            print("  Invalid input. Enter a number.\n")
            continue

        if choice == 0:
            _run_all(client, delay=3)
            return
        elif 1 <= choice <= len(keys):
            name = keys[choice - 1]
            print(f"\n→ Injecting: {name}\n")
            _publish(client, name, SCENARIOS[name])
            return
        else:
            print(f"  Out of range. Pick 0–{len(keys)}.\n")


def _run_all(client: mqtt.Client, delay: float = 5) -> None:
    print(f"\n→ Running all {len(SCENARIOS)} scenarios (delay={delay}s between each)\n")
    for i, (name, scenario) in enumerate(SCENARIOS.items(), 1):
        print(f"[{i}/{len(SCENARIOS)}] {name}")
        _publish(client, name, scenario)
        if i < len(SCENARIOS):
            print(f"  … waiting {delay}s …\n")
            time.sleep(delay)


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Inject mock SRE incident payloads into the Mosquitto MQTTS broker.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host",     default=DEFAULT_HOST,  help="Broker hostname/IP")
    parser.add_argument("--port",     default=DEFAULT_PORT,  type=int, help="Broker port (default 8883)")
    parser.add_argument("--cafile",   default=DEFAULT_CA,    help="Path to CA cert for TLS")
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), help="Name of a single scenario to inject")
    parser.add_argument("--all",      action="store_true",   help="Run all scenarios sequentially")
    parser.add_argument("--repeat",   default=1,             type=int,   help="Repeat the scenario N times")
    parser.add_argument("--delay",    default=5,             type=float, help="Seconds to wait between repeat/all runs")
    parser.add_argument("--list",     action="store_true",   help="Print available scenarios and exit")
    args = parser.parse_args()

    _print_banner()

    if args.list:
        _print_menu()
        return

    print(f"  Broker : {args.host}:{args.port}")
    print(f"  CA cert: {args.cafile}")
    print()

    try:
        client = _build_client(args.host, args.port, args.cafile)
    except Exception as e:
        print(f"❌  Could not connect to broker: {e}")
        sys.exit(1)

    print(f"  Connected ✅\n")

    try:
        if args.all:
            _run_all(client, delay=args.delay)

        elif args.scenario:
            scenario = SCENARIOS[args.scenario]
            print(f"→ Injecting: {args.scenario}\n")
            for run in range(args.repeat):
                if args.repeat > 1:
                    print(f"  Run {run + 1}/{args.repeat}")
                _publish(client, args.scenario, scenario)
                if run < args.repeat - 1:
                    print(f"  … waiting {args.delay}s …\n")
                    time.sleep(args.delay)

        else:
            # No flags → interactive menu
            _interactive_menu(client)

    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
