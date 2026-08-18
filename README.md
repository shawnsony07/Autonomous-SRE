# EdgeOps Agent: Cognitive SRE with Distributed Vector Memory

![EdgeOps Agent Logo](logo.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2.11-purple.svg)](https://github.com/langchain-ai/langgraph)
[![CockroachDB](https://img.shields.io/badge/CockroachDB-Serverless-orange.svg)](https://cockroachlabs.cloud)
[![AWS](https://img.shields.io/badge/AWS-EC2%20|%20S3-FF9900.svg)](https://aws.amazon.com/)

## Table of Contents
- [Overview](#overview)
- [Setup & Configuration](#setup--configuration)
- [Technologies & Services Used](#technologies--services-used)
- [System Architecture](#system-architecture)
- [LangGraph Workflow (Deep Dive)](#langgraph-workflow-deep-dive)
- [Core Technical Components](#core-technical-components)
  - [Hardware Edge Node (ESP32)](#1-hardware-edge-node-esp32)
  - [MQTTS Secure Telemetry Ingestion](#2-mqtts-secure-telemetry-ingestion)
  - [Tiered LLM Routing via LiteLLM](#3-tiered-llm-routing-via-litellm)
  - [Vector Memory (CockroachDB + pgvector)](#4-vector-memory-cockroachdb--pgvector)
  - [Continuous Memory Management](#5-continuous-memory-management)
  - [MCP Tool Execution & Local Interception](#6-mcp-tool-execution--local-interception)
  - [Two-Way HITL Gate (Slack Socket Mode)](#7-two-way-hitl-gate-slack-socket-mode)
  - [Dead Letter Queue (DLQ)](#8-dead-letter-queue-dlq)
  - [Prometheus & Grafana Observability](#9-prometheus--grafana-observability)
  - [LangSmith Tracing](#10-langsmith-tracing)
- [Database Schema](#database-schema)
- [Codebase Structure](#codebase-structure)
- [Extending the Agent](#extending-the-agent)
- [License](#license)

---

## Overview

The **Autonomous SRE Agent** is a next-generation reliability system designed to autonomously ingest, diagnose, and remediate hardware and software anomalies in real time. It bridges the gap between the **IoT edge** and **cloud infrastructure** by combining:

- A real **ESP32 microcontroller** publishing live sensor telemetry over **MQTTS (TLS-encrypted MQTT)**
- A **LangGraph**-orchestrated multi-step reasoning pipeline that mimics an expert Site Reliability Engineer
- Persistent **semantic vector memory** backed by **CockroachDB Serverless** with chronological decay
- Tiered **LLM inference** via a self-hosted **LiteLLM** proxy with circuit breaking and rate limit management
- Strict **Human-in-the-Loop (HITL)** approval gates integrated with a **Slack Socket Mode Bot**
- End-to-end **observability** through **LangSmith** tracing, **Prometheus** metrics, and **Grafana** dashboards

The agent is fully containerized using **Docker Compose** and is deployable on any Linux server (tested on **AWS EC2 t3.micro**).

---

## Setup & Configuration

### Prerequisites
- Docker & Docker Compose (tested on Docker 24+)
- An **AWS EC2 instance** (t3.micro or larger, Amazon Linux 2 / Ubuntu 22.04)
- A **CockroachDB Serverless** cluster with `pgvector` extension enabled
- A **Google Gemini API Key**
- A **Slack App** configured for Socket Mode with `SLACK_APP_TOKEN` and `SLACK_BOT_TOKEN`
- (Optional) AWS credentials for Secrets Manager and S3 archiving

### 1. Clone & Configure Environment
```bash
git clone https://github.com/shawnsony07/Autonomous-SRE.git
cd Autonomous-SRE
cp .env.template .env
# Edit .env with your credentials
```

**Full `.env` reference:**
```env
# Database
COCKROACH_DATABASE_URL="postgresql://user:pass@host:26257/defaultdb?sslmode=verify-full"

# Cloud LLM
OPENAI_BASE_URL="http://litellm:4000"
GEMINI_API_KEY="your-gemini-api-key"

# CockroachDB MCP
COCKROACH_MCP_URL="https://cockroachlabs.cloud/mcp"
COCKROACH_MCP_API_KEY="your-mcp-api-key"
MCP_CLUSTER_ID="your-cluster-id"

# LangSmith Tracing
LANGCHAIN_TRACING_V2="true"
LANGCHAIN_ENDPOINT="https://api.smith.langchain.com"
LANGCHAIN_API_KEY="your-langsmith-key"
LANGCHAIN_PROJECT="Autonomous-SRE-Agent"

# Slack Integration (Socket Mode)
SLACK_APP_TOKEN="xapp-1-..."
SLACK_BOT_TOKEN="xoxb-..."
SLACK_CHANNEL="#sre-alerts"

# AWS
AWS_REGION="ap-south-2"

# MQTT Broker
MQTT_BROKER_HOST="localhost"

# Agent Tuning
LLM_INIT_TIMEOUT_S="30"
LLM_CALL_TIMEOUT_S="120"
ALLOWED_EDGE_COMMANDS="reboot,reset,restart_service,clear_cache"

# Continuous Memory Management
MEMORY_RETENTION_DAYS="90"
AWS_S3_ARCHIVE_BUCKET="your-s3-archive-bucket-name"
```

### 2. Generate MQTTS Certificates
```bash
bash scripts/generate_certs.sh
# Generates: certs/ca.crt, certs/server.crt, certs/server.key
```

### 3. Launch the Full Stack
```bash
docker compose up -d --build
```

This starts 5 services:
| Service | Port | Description |
|---|---|---|
| `sre_agent` | — | Core Python agent (host network) |
| `mosquitto` | 8883 | MQTTS broker |
| `litellm` | 4000 | LLM proxy gateway |
| `prometheus` | 9090 | Metrics scraper |
| `grafana` | 3000 | Dashboard UI |

The agent will automatically:
1. Load secrets from AWS Secrets Manager (if configured)
2. Initialize the database schema and seed vector memory
3. Start the Prometheus metrics server on port 8000
4. Connect to Slack Socket Mode
5. Connect to the Mosquitto broker and begin listening on `sre/edge/telemetry`

### 4. Flash the ESP32 Edge Node

Open `sre-agent-tester/sre-agent-tester.ino` in the **Arduino IDE**. Install the following libraries via Library Manager:
- `PubSubClient` by Nick O'Leary
- `ArduinoJson` by Benoit Blanchon
- `WiFiClientSecure` (bundled with ESP32 board package)

Update the credentials:
```cpp
const char* ssid        = "your-wifi-ssid";
const char* password    = "your-wifi-password";
const char* mqtt_server = "your-ec2-public-ip";
```

Select **Board: XIAO_ESP32S3**, then upload. Open the Serial Monitor at **115200 baud** to confirm MQTTS connection and telemetry publishing.

### 5. Monitor & Verify
```bash
# Follow agent logs
docker compose logs -f sre_agent

# Check all services are healthy
docker compose ps

# Enumerate available MCP tools
docker compose exec sre_agent python list_mcp_tools.py

# Manually inject a test incident
docker compose exec sre_agent python scripts/mock_alerts.py
```

---

## Technologies & Services Used

> [!IMPORTANT]
> ### 🪳 CockroachDB
> - **Distributed Vector Indexing**: `pgvector` is used to store 768-dimensional embeddings of all incident logs and resolutions in the `incident_memory` table. The agent performs Approximate Nearest Neighbor (ANN) semantic search with chronological decay to retrieve context for LLM reasoning.
> - **CockroachDB Cloud Managed MCP Server**: The agent connects to the managed MCP server using `streamable_http` to execute `select_query`, `list_tables`, and `get_table_schema` for root cause analysis and DB operations without writing custom proxy tools.
> 
> ### ☁️ AWS Services
> - **Amazon S3**: Used by the `prune_memory.py` cron job to archive stale semantic memory (older than 90 days) into JSONL files before pruning the database to maintain vector index performance.
> - **AWS Secrets Manager**: Integrated via `src/secrets_manager.py` to securely fetch and inject TLS certificates, LLM API keys, and MCP configuration into the runtime environment without static file mounts.
> - **Amazon EC2**: The Dockerized agent, MQTT broker, and Prometheus/Grafana stack are deployed and run on an AWS EC2 instance.

### 🧠 AI & Orchestration
- **LangGraph & LangChain**: For defining the deterministic, cyclical state machine that drives the agentic reasoning and memory retrieval.
- **Google Gemini**: Using `gemini-3.5-flash` and `gemini-3.5-flash-lite` via LiteLLM for tiered reasoning (fast classification vs. deep planning).
- **LiteLLM**: Self-hosted LLM gateway for rate limiting, failovers, and unified OpenAI-compatible routing.

### 📡 IoT Edge & Messaging
- **Seeed Studio XIAO ESP32-S3**: Physical microcontroller serving as the edge node, written in C++ (Arduino).
- **Mosquitto MQTT**: Secure MQTTS (TLS on port 8883) broker for bi-directional telemetry and command delivery.

### 📊 Observability & Operations
- **Prometheus & Grafana**: Live dashboarding of incident counts and LLM inference latency.
- **LangSmith**: Deep execution tracing, token cost monitoring, and LangGraph workflow introspection.
- **Slack (Socket Mode)**: Real-time, bi-directional Human-in-the-Loop (HITL) approval gates.
- **Docker & Docker Compose**: Full containerization of the agent and infrastructure services.

## System Architecture

The diagram below illustrates the full data flow, from the ESP32 hardware edge all the way through vector memory retrieval, LLM reasoning, Slack approval, and MCP tool execution:

![Architecture Diagram](https://github.com/shawnsony07/Autonomous-SRE/blob/0cbed7d1590e244c3557d4e7a51745016dcb8134/docs/diagrams/architecture.png)

**Data Flow Summary:**
1. The **ESP32 edge node** publishes a JSON telemetry payload over **MQTTS (port 8883)** to the Mosquitto broker running on the EC2 instance.
2. The `src/main.py` MQTT listener thread receives the payload and dispatches it to the async event loop via `asyncio.run_coroutine_threadsafe`.
3. The LangGraph state machine begins execution: `Detect_Ingest → Retrieve_Memory → Reason_Plan → (HITL) → Execute_Skill`.
4. **Remediation commands** flow back to the ESP32 over `sre/edge/commands` MQTT topic, where the firmware executes physical actions (fan relay, CPU throttle, deep sleep).

---

## LangGraph Workflow (Deep Dive)

The LangGraph state machine is a deterministic, typed execution graph. It is compiled with `interrupt_before=["Execute_Skill"]` to forcibly pause and hand control to the HITL gate before any destructive operation.

![LangGraph Workflow Diagram](https://github.com/shawnsony07/Autonomous-SRE/blob/0cbed7d1590e244c3557d4e7a51745016dcb8134/docs/diagrams/langgraph.png)

### State Schema (`AgentState`)
```python
class AgentState(BaseModel):
    incident_id: str           # Unique UUID per incident (used as LangGraph thread_id)
    alert_type: str            # Classified anomaly type (e.g., "Thermal Runaway")
    raw_logs: str              # Raw sensor log string from the ESP32
    historical_context: str   # Nearest-neighbor match from vector memory
    proposed_action: str       # MCP tool name proposed by the LLM
    action_args: dict          # Arguments for the proposed tool
    execution_status: str      # "pending" | "resolved" | "rejected" | "failed_*"
    retry_count: int           # Tracks execution retry count (max 3)
```

### Node Execution Details

#### `Detect_Ingest`
- Uses `sre-fast-tier` (Flash Lite, 15 RPM) to classify the raw log string into a structured `alert_type`.
- JSON extraction uses a robust forward-scan parsing strategy to handle partial or malformed LLM JSON outputs.
- Times out after `LLM_CALL_TIMEOUT` seconds (configurable via `LLM_CALL_TIMEOUT_S` env var).

#### `Retrieve_Memory`
- Embeds the raw log using `nomic-embed-text` (768-dim) via Ollama.
- Executes a CTE-based pgvector ANN query with **chronological temporal decay**: records older than 30 days receive a `+0.2` distance penalty, preventing stale topological data from dominating.
- Falls back gracefully if the DB pool is unavailable.

#### `Reason_Plan`
- Uses `sre-complex-tier` (Flash, 5 RPM) for deep reasoning.
- Injects both `raw_logs` and `historical_context` into a few-shot system prompt.
- Extracts a `tool_name` + `arguments` JSON object from the LLM response.
- LLM latency is measured and exported to Prometheus (`LLM_LATENCY_HISTOGRAM`).

#### `Execute_Skill`
- Checks `execution_status` for `"rejected"` or `"failed_planning"` — short-circuits immediately if either is set.
- Calls `run_mcp_tool()` from `src/tools.py` with the proposed action and arguments.
- On failure, increments `retry_count`; if `retry_count < 3`, the graph loops back to `Reason_Plan`.

---

## Core Technical Components

### 1. Hardware Edge Node (ESP32)

> [!NOTE]
> **Cognitive SRE vs. Deterministic Control (Mock Setup Disclaimer)**  
> While the ESP32 hardware interactions shown here (like turning on a fan) serve as a tangible proxy to demonstrate the end-to-end communication pipeline, you might wonder: *why not just use a local `if (temp > 85) { turn_on_fan(); }` rule?*  
> An ESP32 can easily turn on its own fan locally, but this project builds a **cognitive engine for when local if/else statements fail**:
> - **Treating the Root Cause**: A microcontroller only knows it's hot, not *why*. The SRE agent analyzes logs to deduce if the heat is from a software lockup—realizing that a `THROTTLE_CPU` command or rollback is the true fix, not just spinning a fan.
> - **Fleet-Wide Context**: If 50 nodes get hot and turn on fans simultaneously, it could brown out the entire swarm. The central agent sees the global state and orchestrates staggered, safe responses.
> - **Vector Memory**: Microcontrollers lack long-term memory. The agent leverages CockroachDB's vector search to learn from past mistakes.
> - **Human-In-The-Loop**: The agent catches critical anomalies and securely asks for human permission before executing.
>
> **Visualizing the Agent**: We integrated an OLED display into the ESP32 code so you can physically see the hardware state changes and watch exactly how the SRE agent is communicating with the edge in real time.
>
> **No Hardware? No Problem**: If you don't have an ESP32 or an OLED display lying around, you can still test the entire system! Simply use the included developer utility `scripts/mock_alerts.py` to inject synthetic anomalies directly into the Mosquitto broker and watch the agent resolve them.

The physical edge hardware is a **Seeed Studio XIAO ESP32-S3** flashed with the firmware in `sre-agent-tester/sre-agent-tester.ino`. It uses three Arduino libraries:

- **`WiFi.h`** — Station mode Wi-Fi connection
- **`WiFiClientSecure.h`** — TLS socket wrapper for MQTTS
- **`PubSubClient.h`** — MQTT protocol client
- **`ArduinoJson.h`** — JSON serialization for telemetry payloads

**Key firmware behaviours:**

| Feature | Detail |
|---|---|
| MQTTS Port | `8883` (encrypted, `espClient.setInsecure()` skips cert validation for EC2 IPs) |
| Telemetry Topic | `sre/edge/telemetry` |
| Command Topic | `sre/edge/commands` |
| Publish Interval | Every 30 seconds |
| Client ID | `XIAO-ESP32S3-<random_hex>` (unique per connection) |

**Telemetry JSON Payload:**
```json
{
  "id": "xiao-esp32s3-node-01",
  "alert_type": "Thermal Runaway",
  "raw_logs": "ESP32 thermal sensor reports temperature exceeding 85C for 60 seconds."
}
```

**Inbound Remediation Commands:**
The firmware listens on `sre/edge/commands` and reacts to commands dispatched by the SRE Agent:

| Command | Firmware Action |
|---|---|
| `FAN_ON` | Activates cooling fan relay via GPIO |
| `THROTTLE_CPU` | `setCpuFrequencyMhz(80)` — reduces heat output |
| `RESET_I2C` | Toggles I2C bus power pin to re-initialize stuck sensors |
| `DEEP_SLEEP` | Enters low-power deep sleep mode |

**Live ESP32 execution log (captured from Serial Monitor):**

![ESP32 Edge Node Execution Log](docs/images/esp32-edge-node-execution-log.png)

---

### 2. MQTTS Secure Telemetry Ingestion

The Mosquitto MQTT broker runs inside Docker on **port 8883** with TLS enforced. The server certificate is auto-generated by `scripts/generate_certs.sh` and mounted into the container at `/mosquitto/certs/`.

**`mosquitto.conf`:**
```
listener 8883
cafile /mosquitto/certs/ca.crt
certfile /mosquitto/certs/server.crt
keyfile /mosquitto/certs/server.key
require_certificate false
```

The agent's MQTT listener in `src/main.py` uses `paho-mqtt` with TLS configured to trust the same CA certificate:
```python
client.tls_set(ca_certs="certs/ca.crt")
await asyncio.to_thread(client.connect, mqtt_host, 8883, 60)
client.loop_start()
```

> **Note:** `client.connect()` is a blocking TCP call and is explicitly offloaded to a thread via `asyncio.to_thread()` to prevent stalling the `asyncio` event loop. Subscriptions are re-established inside `on_connect` to survive broker reconnections.

AWS Secrets Manager integration (`src/secrets_manager.py`) can be used to dynamically inject certificate paths and TLS credentials at startup, replacing static file mounts.

---

### 3. Tiered LLM Routing via LiteLLM

All LLM inference is proxied through a self-hosted **LiteLLM** gateway running as a Docker service on port `4000`. This provides:
- **Rate limit management** (RPM/TPM quotas per tier)
- **Automatic fallbacks** between models within the same tier
- **Graceful circuit breaking** on repeated failures (`allowed_fails: 2`, `cooldown_time: 60s`)
- **A unified OpenAI-compatible API** so the agent code requires zero changes when swapping underlying models

**`litellm_config.yaml` — Tier Configuration:**

| Tier Alias | Models | RPM | Use Case |
|---|---|---|---|
| `sre-fast-tier` | `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite` | 15 | Log classification, anomaly categorization |
| `sre-complex-tier` | `gemini-3.6-flash`, `gemini-3.5-flash` | 5 | Root cause analysis, remediation planning |
| `local-fallback` | `ollama/llama3.2:1b` | — | Local fallback if cloud APIs are unavailable |

**Router Settings:**
```yaml
router_settings:
  routing_strategy: simple-shuffle
  enable_pre_call_checks: true
  allowed_fails: 2
  cooldown_time: 60
  fallbacks: [{"sre-complex-tier": ["local-fallback"]}]
```

The `aget_active_llm()` function in `src/llm_factory.py` maintains a cached `ChatOpenAI` instance per tier. It health-probes the gateway with a dummy invocation at startup and applies a configurable wall-clock timeout (`LLM_INIT_TIMEOUT_S`) to prevent gateway delays from blocking the async event loop.

---

### 4. Vector Memory (CockroachDB + pgvector)

The `incident_memory` table is the agent's long-term semantic memory. It stores past incidents as 768-dimensional vector embeddings, enabling approximate nearest-neighbor (ANN) semantic search to retrieve relevant historical resolutions.

**Schema:**
```sql
CREATE TABLE incident_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_type TEXT,
    error_log TEXT,
    resolution_steps TEXT,
    embedding VECTOR(768),
    created_at TIMESTAMP DEFAULT current_timestamp()
);

CREATE VECTOR INDEX incident_memory_embedding_idx ON incident_memory (embedding);
```

**CockroachDB live vector memory view:**

![CockroachDB Live Vector Memory](docs/images/cockroachdb-live-vector-memory.png)

**Database tables overview:**

![CockroachDB Database Tables Overview](docs/images/cockroachdb-database-tables-overview.png)

**Table schema detail:**

![CockroachDB Vector Table Schema](docs/images/cockroachdb-vector-table-schema.png)

**Distributed vector index (CockroachDB native ANN):**

![CockroachDB Distributed Vector Index](docs/images/cockroachdb-distributed-vector-index.png)

**ANN Query with Temporal Decay (`src/graph.py`):**
```sql
WITH q AS (SELECT %s::vector AS v)
SELECT error_log, resolution_steps
FROM incident_memory, q
WHERE embedding <=> q.v < 0.25
ORDER BY 
    (embedding <=> q.v) + CASE WHEN created_at < current_timestamp() - INTERVAL '30 days' THEN 0.2 ELSE 0.0 END ASC,
    created_at DESC
LIMIT 1
```

This query applies a strict cosine distance threshold of `0.25`. Records older than 30 days incur an additional `+0.2` penalty — effectively excluding them from results unless they are near-perfect matches (`< 0.05` raw distance). In the event of a tie, the most recent record wins.

**Pre-seeded Incidents:**
On first boot, the agent seeds three synthetic historical incidents to bootstrap vector memory:

| Alert Type | Error Log | Resolution |
|---|---|---|
| Thermal Runaway | ESP32 temperature exceeding 85°C for 60s | Fan ON + CPU throttle to 80MHz |
| Sensor Disconnect | I2C bus timeout reading BMP280 | Reset I2C bus + re-initialize driver |
| Voltage Drop | VCC rail dipped below 2.9V | Disable peripherals + deep sleep |

---

### 5. Continuous Memory Management

As the `incident_memory` table grows, the `scripts/prune_memory.py` cron script prevents vector index degradation by archiving and pruning stale records.

**Prune Workflow:**
1. Connects to CockroachDB via `psycopg_pool.AsyncConnectionPool`
2. Queries records where `created_at < NOW() - INTERVAL '<MEMORY_RETENTION_DAYS> days'`
3. Serializes the full records (including the vector embedding) into a `.jsonl` file
4. Uploads the archive to **AWS S3** using `boto3.upload_file()` (offloaded via `asyncio.to_thread`)
5. **Only after confirmed S3 upload** — executes `DELETE FROM incident_memory WHERE id = ANY(%s)` to purge the exported records

> **Safety Guarantee:** If the S3 upload fails for any reason (network timeout, permission denied, rate limit), the script aborts immediately and the database rows are never deleted, preventing irrecoverable data loss.

**Environment Variables Required:**
```env
MEMORY_RETENTION_DAYS=90
AWS_S3_ARCHIVE_BUCKET=your-archive-bucket-name
```

**Recommended cron schedule:**
```bash
0 2 * * * cd /home/ubuntu/Autonomous-SRE && python3 scripts/prune_memory.py >> /var/log/prune_memory.log 2>&1
```

---

### 6. MCP Tool Execution & Local Interception

The agent executes infrastructure changes using the **Model Context Protocol (MCP)** — a typed, session-oriented RPC framework.

**Available MCP tools on the CockroachDB managed endpoint:**

| Tool | Description |
|---|---|
| `select_query` | Read-only SELECT (auto-LIMIT 25) |
| `list_databases` | List all databases |
| `list_tables` | List tables in a database |
| `get_table_schema` | Schema + index info for a table |
| `explain_query` | Query plan without executing |
| `create_database` | Create a new database |
| `create_table` | DDL-only CREATE TABLE statements |
| `insert_rows` | INSERT INTO ... VALUES / SELECT |
| `get_cluster` | Cluster metadata |
| `list_clusters` | All accessible clusters |

**Transport Layer Fix:**  
The managed CockroachDB MCP endpoint requires strict **HTTP POST** requests. Standard SSE (`sse_client`) sends GET requests, causing HTTP 405 errors. The agent uses `streamablehttp_client` / `http_client` (falling back through import aliases for SDK version compatibility):

```python
try:
    from mcp.client.http import http_client
except ImportError:
    try:
        from mcp.client.streamable_http import streamablehttp_client as http_client
    except ImportError:
        from mcp.client.streamable_http import streamable_http_client as http_client
```

**Local DDL Interception:**  
Because the managed MCP is read-only/safe-DML only and does not expose `DROP TABLE` or administrative DDL, the agent intercepts the `execute_query` tool call in `src/tools.py` and routes it to the local `psycopg_pool` executor:

```python
if tool_name == "execute_query":
    pool = get_pool()
    query = safe_args.get("query", "")
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(query)
        await conn.commit()
```

**ExceptionGroup Unmasking:**  
`asyncio.TaskGroup` wraps nested errors in `ExceptionGroup`, which standard `except Exception` blocks cannot catch directly. The agent explicitly catches `ExceptionGroup` and uses `traceback.format_exception` to fully unwrap and expose the true inner HTTP or MCP error:

```python
except ExceptionGroup as eg:
    full_trace = "".join(traceback.format_exception(type(eg), eg, eg.__traceback__))
    raise RuntimeError(f"MCP inner error:\n{full_trace}")
```

**Edge Command Allowlist:**  
To prevent LLM hallucination from escalating to arbitrary command injection, the `publish_edge_command` tool strictly validates commands against an allowlist:
```python
ALLOWED_EDGE_COMMANDS = {"FAN_ON", "FAN_OFF", "RELAY_TRIGGER", "RELAY_RESET",
                          "DEEP_SLEEP", "WAKE", "THROTTLE_CPU", "RESET_I2C"}
```

---

### 7. Two-Way HITL Gate (Slack Socket Mode)

When the LangGraph workflow reaches a destructive operation (any action where `write_consent: True`), execution is forcibly paused via `interrupt_before=["Execute_Skill"]`. The operator must approve or deny within **120 seconds**.

**Approval is received via two simultaneous channels:**

```python
done, pending = await asyncio.wait(
    [terminal_task, slack_future],
    timeout=120.0,
    return_when=asyncio.FIRST_COMPLETED
)
```

The first channel to respond wins. If neither responds within 120s, the action is auto-denied.

**Live end-to-end agent execution — normal (non-destructive) remediation path:**

This capture shows the agent's full startup sequence: secrets loaded from AWS Secrets Manager, CockroachDB initialized, vector memory seeded, Prometheus started, MQTT connected. Two incidents (`Thermal Runaway` / `Thermal Sensor Overheating`) arrive from the ESP32. The `Retrieve_Memory` node finds a historical match, `Reason_Plan` generates `publish_edge_command` with args `{command: FAN_ON}`, and `Execute_Skill` dispatches the MQTT command — workflow resolves without HITL because no `write_consent: True` flag was set.

![Terminal Live Telemetry Workflow](docs/images/terminal-live-telemetry-workflow.png)

**Slack Block Kit HITL notification — destructive action pending:**

The `#sre-alerts` channel shows the bot posting an `⚠️ Autonomous SRE Agent HITL Alert ⚠️` Block Kit message for incident `TEST-DB-999`. The action is `execute_query` with args `{"query": "DROP TABLE old_metrics", "write_consent": true}`. The message displays two interactive buttons — **Approve** (green) and **Deny** (red) — and updates to `✅ Approved by @Shawn Sony` after the button is clicked. The channel history also shows multiple prior approvals from the same development session.

![Slack Interactive Approval Gate](docs/images/slack-interactive-approval-gate.png)

The Slack Bot (`src/slack_bot.py`) uses `slack_bolt` with **Socket Mode** — no public webhook endpoint is required. The bot establishes a persistent WebSocket connection to Slack's infrastructure and receives events in real time.

- `@app.action("hitl_approve")` — Sets the incident's `asyncio.Future` result to `'y'`, unblocking the event loop.
- `@app.action("hitl_deny")` — Sets the result to `'n'`, causing the graph to update state to `"rejected"` and terminate.
- The Slack message is replaced with a confirmation (✅ Approved / ❌ Denied) after the button is clicked.

**Full terminal HITL approval workflow — destructive path with Slack approval:**

This capture shows the complete HITL path for incident `TEST-DB-999`. The agent detects `Unrecoverable legacy table corruption`, fails to find a historical match (cold-start), plans `execute_query {DROP TABLE old_metrics, write_consent: True}`, and pauses. It dispatches the Slack Block Kit alert and races Slack against terminal. The log shows `-> Approval received from Slack: y` — the operator clicked Approve in Slack. `Execute_Skill` then runs the intercepted local DDL: `Successfully executed query: DROP TABLE old_metrics`. Workflow completes.

![Terminal HITL Approval Workflow](docs/images/terminal-hitl-approval-workflow.png)

---

### 8. Dead Letter Queue (DLQ)

The DLQ is a safety net for incidents that the SRE agent cannot remediate after exhausting retries. Any incident that causes an unhandled exception is serialized to the `dead_letter_queue` table in CockroachDB for post-mortem analysis.

**Schema:**
```sql
CREATE TABLE dead_letter_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payload JSONB,
    error_reason TEXT,
    created_at TIMESTAMP DEFAULT current_timestamp()
);
```

**DLQ routing** is triggered from two places:
1. `_log_future_error` callback — fires if `process_incident` itself raises an exception.
2. `process_incident()` catch block — fires on any `CRITICAL ERROR` during LangGraph execution.

The `scripts/replay_dlq.py` script can retrieve failed payloads and re-inject them into the live agent for manual disaster recovery replay.

---

### 9. Prometheus & Grafana Observability

The agent exposes a Prometheus metrics server on **port 8000** using `prometheus_client.start_http_server`.

**Registered Metrics (`src/metrics.py`):**

| Metric | Type | Labels | Description |
|---|---|---|---|
| `sre_incidents_total` | Counter | `alert_type`, `resolution_status` | Total incidents processed, by type and outcome |
| `sre_llm_latency_seconds` | Histogram | — | Distribution of LLM call durations |

**Grafana dashboards (live):**

**Incident counter by type and status:**

![Grafana Incident Counter Dashboard](docs/images/grafana-incident-counter-dashboard.png)

**LLM inference latency histogram:**

![Grafana LLM Latency Histogram](docs/images/grafana-llm-latency-histogram.png)

**`prometheus.yml` scrape config:**
```yaml
scrape_configs:
  - job_name: 'sre_agent'
    static_configs:
      - targets: ['localhost:8000']
```

---

### 10. LangSmith Tracing

Every LangGraph node execution, LLM invocation, and tool call is automatically traced via LangSmith when `LANGCHAIN_TRACING_V2=true` is set.

**LangSmith Monitoring dashboard — Trace Count, Trace Latency (P50/P99), and Trace Error Rate over time:**

The top-level monitoring view for the `Autonomous-SRE-Agent` project. Trace Count shows bursts of activity on August 16–18. Trace Latency P99 spikes to ~120s during complex-tier LLM calls (matching `LLM_CALL_TIMEOUT_S=120`). Error rate spikes correspond to failed MCP transport attempts during the debugging phase.

![LangSmith Observability Dashboard](docs/images/langsmith-observability-dashboard.png)

**LangSmith Waterfall Trace — full incident resolution breakdown per node:**

The Waterfall view for a single LangGraph thread turn (Turn 11, 15.90s total, 888 tokens). The trace is broken down into: `Detect_Ingest` → `ChatOpenAI` (7.28s, fast-tier classification), `Retrieve_Memory` (0.79s, vector ANN search), `Reason_Plan` → `ChatOpenAI` (5.37s + 2.46s, complex-tier reasoning). The right panel shows the `AgentState` mid-execution: `alert_type: Thermal Runaway`, `incident_id: xiao-esp32s3-node-01`, `proposed_action: publish_edge_command`, `action_args: {command: FAN_ON}`.

![LangSmith LangGraph Waterfall Trace](docs/images/langsmith-langgraph-waterfall-trace.png)

**LangSmith Run Types — per-node run counts, median latency, and error rate over time:**

This view (LangSmith Feedback Scores / Run Types tab) shows depth-1 node-level statistics for all four LangGraph nodes: `Detect_Ingest`, `Execute_Skill`, `Reason_Plan`, `Retrieve_Memory`. The left chart shows run counts per node; the right shows **median latency** per node over time — `Reason_Plan` dominates at ~30s peaks, confirming the complex-tier LLM is the primary bottleneck. The error rate chart shows `Reason_Plan` had a ~25% error rate spike during LiteLLM debugging.

![LangSmith Node Execution Stats](docs/images/langsmith-node-execution-stats.png)

**LangSmith LLM Calls tab — LLM call count, P50/P99 latency, total cost, and output tokens over time:**

This combined view covers the **LLM Calls** and **Cost & Tokens** sections. LLM Count shows bursts matching the Trace Count. LLM Latency P99 reaches ~120s (gateway timeout ceiling). Total Cost chart shows cumulative inference spend across the session. Output Tokens chart shows token consumption spikes during complex reasoning phases, peaking at ~6K tokens per incident during the database corruption scenario.

![LangSmith LLM Latency Metrics](docs/images/langsmith-llm-latency-metrics.png)

**LangSmith Cost & Tokens — dedicated token breakdown (output tokens, input tokens, per-trace percentiles):**

The full Cost & Tokens tab showing **Output Tokens** (total and P50/P99 per trace), **Input Tokens** (total and P50/P99 per trace). Output token P99 reaches ~400 tokens/trace and Input P99 reaches ~5K tokens/trace, confirming that the system prompt with historical context injection in `Reason_Plan` is the dominant cost driver.

![LangSmith Token Usage Metrics](docs/images/langsmith-token-usage-metrics.png)

**LangSmith Turns view — per-turn AgentState inputs and outputs for a 14-turn thread:**

This shows the Turns view for a complete LangGraph thread handling multiple back-to-back incidents (14 turns total). Each turn expands to show the full `AgentState` input and output at every node transition. Turn 13 shows `Retrieve_Memory` output: `historical_context: Similar Incident Log: ESP32 thermal sensor reports temperature exceeding 85C for 60 seconds. Resolution Used: Triggered active cooling fans...`. Turn 14 inputs `null` (the HITL resume stream), confirming the graph correctly resumed after the interrupt.

![LangSmith Turns Execution View](docs/images/langsmith-turns-execution-view.png)

**LangSmith final resolved AgentState — execution_status: resolved, action: FAN_ON:**

This is the full output state payload for the last turn of a completed incident resolution. The right panel (Details view, Turn 14) shows the final `AgentState` after `Execute_Skill` ran: `alert_type: Critical thermal sensor overheating`, `execution_status: resolved`, `historical_context: Similar Incident Log...`, `incident_id: xiao-esp32s3-node-01`, `proposed_action: publish_edge_command`, `action_args: {command: FAN_ON}`, `retry_count: 0`. This confirms the full end-to-end Thermal Runaway → FAN_ON remediation resolved in a single LangGraph thread with zero retries.

![LangSmith Agent State Payload](docs/images/langsmith-agent-state-payload.png)

---

## Database Schema

```
CockroachDB Serverless
├── incident_memory
│   ├── id            UUID PK (gen_random_uuid())
│   ├── alert_type    TEXT
│   ├── error_log     TEXT
│   ├── resolution_steps TEXT
│   ├── embedding     VECTOR(768)  — 768-dim Gemini / nomic-embed-text
│   └── created_at    TIMESTAMP DEFAULT current_timestamp()
│
└── dead_letter_queue
    ├── id            UUID PK (gen_random_uuid())
    ├── payload       JSONB         — full incident payload
    ├── error_reason  TEXT
    └── created_at    TIMESTAMP DEFAULT current_timestamp()
```

> **Important:** The `pgvector` extension must be enabled by a CockroachDB superuser before first run:
> ```sql
> CREATE EXTENSION IF NOT EXISTS vector;
> ```

---

## Codebase Structure

```text
Autonomous SRE/
├── docker-compose.yml           # Orchestrates 5 services: sre_agent, mosquitto, litellm, prometheus, grafana
├── litellm_config.yaml          # Two-tier LLM routing config with fallback and rate limits
├── mosquitto.conf               # MQTTS broker config (port 8883, TLS enforcement)
├── prometheus.yml               # Prometheus scrape config (sre_agent:8000)
├── requirements.txt             # Python dependencies (pinned versions)
├── list_mcp_tools.py            # Developer utility: enumerates tools on the CockroachDB MCP endpoint
├── .env.template                # Reference template for all required environment variables
├── LICENSE                      # MIT License
│
├── docs/
│   ├── diagrams/                # Mermaid source (.mmd) + rendered PNGs
│   │   ├── architecture.mmd     # Source: Mermaid architecture flowchart
│   │   ├── architecture.png     # Rendered: System architecture diagram
│   │   ├── langgraph.mmd        # Source: Mermaid LangGraph state machine
│   │   └── langgraph.png        # Rendered: LangGraph execution flow
│   └── images/                  # Live screenshots and observability captures
│       ├── cockroachdb-live-vector-memory.png
│       ├── cockroachdb-database-tables-overview.png
│       ├── cockroachdb-vector-table-schema.png
│       ├── cockroachdb-distributed-vector-index.png
│       ├── esp32-edge-node-execution-log.png
│       ├── grafana-incident-counter-dashboard.png
│       ├── grafana-llm-latency-histogram.png
│       ├── langsmith-observability-dashboard.png
│       ├── langsmith-langgraph-waterfall-trace.png
│       ├── langsmith-node-execution-stats.png
│       ├── langsmith-llm-latency-metrics.png
│       ├── langsmith-token-usage-metrics.png
│       ├── langsmith-turns-execution-view.png
│       ├── langsmith-agent-state-payload.png
│       ├── slack-interactive-approval-gate.png
│       ├── terminal-hitl-approval-workflow.png
│       └── terminal-live-telemetry-workflow.png
│
├── sre-agent-tester/
│   └── sre-agent-tester.ino     # Arduino firmware for Seeed XIAO ESP32-S3
│
├── scripts/
│   ├── generate_certs.sh        # Auto-generates self-signed TLS certs for Mosquitto
│   ├── prune_memory.py          # Cron: archives + prunes old vector memory to AWS S3
│   ├── replay_dlq.py            # Disaster recovery: replays failed DLQ payloads
│   └── mock_alerts.py           # Developer utility: injects synthetic anomalies
│
└── src/                         # Core SRE Agent Python package
    ├── __init__.py
    ├── main.py                  # Entrypoint: MQTT TLS listener, LangGraph runner, Slack init, DLQ routing
    ├── graph.py                 # LangGraph state machine (4 nodes + retry edges)
    ├── database.py              # CockroachDB pool, schema init, seeding, DLQ write
    ├── llm_factory.py           # LiteLLM proxy factory, LLM health probe, embedding cache
    ├── tools.py                 # MCP execution, local DDL interception, MQTT publisher, allowlist
    ├── prompts.py               # Few-shot system prompts for all LLM nodes
    ├── metrics.py               # Prometheus Counter and Histogram definitions
    ├── secrets_manager.py       # AWS Secrets Manager integration for TLS/secret injection
    └── slack_bot.py             # Slack Socket Mode bot, Block Kit HITL alerts, future registry
```

---

## Extending the Agent

### Adding New MCP Tools
1. Define a wrapper function in `src/tools.py` decorated with `@retry(...)` for resilience.
2. If the target MCP endpoint blocks the tool server-side, add an interception block in `run_mcp_tool()` routing to the local `psycopg_pool` or any other async executor.
3. Update the allowlist in `ALLOWED_EDGE_COMMANDS` for any new edge hardware commands.
4. Add the tool's schema to the `REASON_PLAN_SYSTEM_PROMPT` in `src/prompts.py` so the LLM knows how to invoke it.

### Extending the LangGraph Workflow
The state machine in `src/graph.py` is fully modular. To add a `Verify_Fix` node after `Execute_Skill`:
```python
async def verify_fix_node(state: AgentState) -> AgentState:
    # Use sre-fast-tier to verify the fix was applied correctly
    ...
    return {"execution_status": "verified"}

workflow.add_node("Verify_Fix", verify_fix_node)
workflow.add_edge("Execute_Skill", "Verify_Fix")
# Remove the old Execute_Skill -> END edge and add Verify_Fix -> END
```

### Supporting Additional Edge Hardware
The telemetry ingestion is hardware-agnostic. Any device that can publish a JSON payload to `sre/edge/telemetry` with `id`, `alert_type`, and `raw_logs` fields over MQTT will integrate automatically.

---

## License

This project is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.
