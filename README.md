# EdgeOps Agent: Cognitive SRE with Distributed Vector Memory

![EdgeOps Agent Logo](logo.png)

## Overview

The Autonomous SRE Agent is a next-generation reliability system designed to autonomously ingest, diagnose, and remediate hardware and software anomalies in real time. It leverages a tiered Large Language Model (LLM) architecture via LiteLLM, persistent vector memory backed by CockroachDB Serverless, and direct edge-device interaction through MQTT. 

When anomalies are detected, the agent uses LangGraph to orchestrate a reasoning pipeline that mimics an expert Site Reliability Engineer (SRE). It retrieves historical incidents via semantic search (optimized with chronological decay), synthesizes root cause analyses, and executes remediation tools via the Model Context Protocol (MCP) using a safe local interception mechanism, all while enforcing a strict Human-in-the-Loop (HITL) gate with two-way Slack Socket Mode integration for destructive operations.

## Architecture

![Architecture Diagram](docs/diagrams/architecture.png)

## Core Technical Components & Deep Dive

### 1. LangGraph Workflow (The Brain)
The heart of the SRE Agent is a deterministic state machine built with LangGraph. It processes incidents through a strictly defined execution graph:

![LangGraph Workflow Diagram](docs/diagrams/langgraph.png)

The deterministic execution loop relies on asynchronous transitions. `Detect_Ingest` parses the incident payload. The state is then passed to `Retrieve_Memory`, and subsequently `Reason_Plan` constructs an LLM tool-calling prompt. If a remediation tool requires destructive modifications, execution stalls at `Execute_Skill` until the `HITL_Gate` grants explicit permission via dual-channel verification (Slack or Terminal).

### 2. Tiered LLM Routing via LiteLLM
To balance latency, cost, and intelligence, the agent routes requests through a LiteLLM gateway acting as a proxy layer with built-in circuit breakers.
- **`sre-fast-tier`**: High-frequency, low-latency tasks (e.g., initial log parsing, anomaly categorization). Mapped to fast foundational models (like Flash Lite).
- **`sre-complex-tier`**: Deep reasoning tasks (e.g., root cause analysis, script generation). Mapped to dense reasoning models (like Flash).
- **Embeddings & Fallback**: Directly interfaces with Gemini's `text-embedding-004` API via asynchronous REST calls for low-latency vector generation, deprecating previous local-only dependencies (Ollama) to ensure cross-platform consistency and zero memory bloat. Initialization timeouts (`LLM_INIT_TIMEOUT_S`) are strictly tuned to handle cold starts.

### 3. Continuous Memory Management (CockroachDB Serverless)
The agent stores and recalls past incidents using a `pgvector` enabled schema on a CockroachDB Serverless cluster.
- **768-Dimension Embeddings**: Text logs and resolution steps are embedded to support semantic search.
- **Chronological Memory Decay**: The vector index search leverages a specialized SQL CTE. Instead of pure distance queries (`embedding <=> q.v < 0.25`), the retrieval applies a strict temporal decay penalty (e.g., `+ 0.2`) to incidents older than 30 days. This prioritizes recent topological data while retaining the ability to recall near-perfect historical matches, falling back to a `created_at DESC` sort tie-breaker.
- **Automated S3 Archiving (Pruning)**: To prevent vector bloat, an asynchronous cron job (`scripts/prune_memory.py`) safely exports decayed records to an AWS S3 bucket using `boto3` JSONL streaming, strictly committing destructive `DELETE FROM incident_memory` statements only upon a validated S3 HTTP 200 upload response.

### 4. Edge Telemetry & Disaster Recovery (MQTT & DLQ)
Real-time telemetry from edge hardware (like ESP32 thermistors or voltage sensors) is ingested via `paho.mqtt`.
- **MQTTS TLS Security**: The Mosquitto broker strictly listens on port 8883, relying on auto-generated certificates managed and rotated via AWS Secrets Manager.
- **Dead Letter Queue (DLQ)**: The ingestion pipeline incorporates a DLQ for unresolvable incidents and poison pills. When the SRE graph fails to remediate an issue after configured retry limits, the state payload is serialized to a discrete DLQ stream for post-mortem analysis and manual disaster recovery replay.

### 5. Advanced Action Execution (MCP & Local Interception)
The agent executes infrastructure and database changes securely via the **Model Context Protocol (MCP)**.
- Integrates with the CockroachDB Managed MCP endpoint for schema introspection and tool enumeration.
- **Protocol Reversion & Streamable Transport**: Standard Server-Sent Events (SSE) constraints were circumvented by enforcing a strict POST-based `streamable_http_client` over HTTP/1.1 to comply with server routing constraints.
- **`ExceptionGroup` Unmasking**: Deep nested network exceptions inside `asyncio.TaskGroup` structures are fully unmasked using stack traversal, ensuring accurate `MCPError` propagation.
- **Local DDL Interception**: Because managed MCP endpoints restrict destructive DDL/DML, the agent intelligently intercepts critical tool calls (like `execute_query`) within `src/tools.py` and transparently routes them to the local `psycopg_pool` asynchronous executor.

### 6. Two-Way Human-In-The-Loop (HITL) Gate
Destructive or configuration-altering actions trigger an automatic pause in the LangGraph workflow.
- **Slack Socket Mode**: Real-time integration using `slack_bolt` and `aiohttp`. Instead of exposing a public webhook, the agent establishes a persistent WebSocket connection to Slack, dispatching Block Kit interactive modals (Approve/Reject) directly to the `#sre-alerts` channel.
- **Asynchronous Racing**: The state machine utilizes `asyncio.wait(return_when=asyncio.FIRST_COMPLETED)` to simultaneously poll the local terminal `sys.stdin.readline` stream and the remote Slack Socket payload. The first affirmative response unlocks the node, while unhandled timeouts trigger immediate transaction abortion.

### 7. LangSmith Tracing & Observability
- Deep integration with `langsmith` enables 100% trace coverage across the execution graph. Every LLM generation, tool call latency, and node transition is recorded.
- Infrastructure performance is exported via Prometheus metrics and visualized inside local Grafana dashboards.

## Codebase Structure

```text
Autonomous SRE/
├── docker-compose.yml       # Orchestrates the SRE Agent, Mosquitto, LiteLLM, Prometheus, Grafana
├── litellm_config.yaml      # Configures tiered routing and RPM quotas
├── mosquitto.conf           # Secure MQTT Broker configuration (Port 8883)
├── .env                     # Secrets, AWS credentials, S3 buckets, and endpoint configurations
├── docs/
│   └── diagrams/            # Generated architecture and LangGraph Mermaid PNG assets
├── scripts/
│   ├── prune_memory.py      # Automated S3 archiving and chronological decay cleanup
│   └── mock_alerts.py       # Developer utility for local anomaly injection
└── src/                     # Core Agent Source Code
    ├── main.py              # Entry point: MQTT TLS listener, LangGraph execution, dual-channel HITL
    ├── graph.py             # LangGraph state machine definition, chronological decay search
    ├── database.py          # CockroachDB connection pooling, schema migrations, pgvector indexing
    ├── llm_factory.py       # LiteLLM proxying, Gemini REST fallback, async circuit breakers
    ├── tools.py             # MCP execution, HTTP transport overrides, ExceptionGroup unmasking
    └── prompts.py           # Few-shot prompts for root cause synthesis and tool mapping
```

## Setup & Configuration

### Prerequisites
- Docker & Docker Compose
- A CockroachDB Serverless Cluster (with `pgvector` manually enabled by a superuser)
- Google Gemini API Key
- AWS Credentials (for Secrets Manager and S3 Archiving)
- Slack App Token (`xapp-...`) and Bot Token (`xoxb-...`)

### 1. Environment Variables
Create a `.env` file based on `.env.template`:
```env
GEMINI_API_KEY=your_google_api_key
COCKROACH_DATABASE_URL=postgresql://user:password@host:26257/defaultdb?sslmode=verify-full
COCKROACH_MCP_URL=https://mock-mcp-server.cockroachlabs.cloud/sse
COCKROACH_MCP_API_KEY=your_mcp_api_key
SLACK_APP_TOKEN=xapp-1-your-app-token
SLACK_BOT_TOKEN=xoxb-your-bot-token
AWS_S3_ARCHIVE_BUCKET=your-archive-bucket
MEMORY_RETENTION_DAYS=90
```

### 2. Launch the System
Start the entire stack using Docker Compose:
```bash
docker compose up --build
```
This command spins up the local MQTT broker, the LiteLLM gateway, the Prometheus/Grafana observability stack, and the SRE Agent itself. The agent will automatically initialize the database schema, migrate the `created_at` timestamp if necessary, warm up the LLM connections, bind to the Slack WebSocket, and begin listening for telemetry.

## Extending the Agent

### Adding New MCP Tools
To expand the agent's capabilities (e.g., integrating with Datadog or Kubernetes), add new MCP tool schemas in `src/tools.py`.
1. Define the tool wrapper using `@tool`.
2. Connect to the respective MCP server using the `mcp.client.session.ClientSession`.
3. If the tool is highly destructive or blocked by the remote server (e.g., dropping databases), intercept the execution in `_execute_mcp_call()` to execute it securely via the local executor pool.

### Extending the LangGraph Workflow
The state machine in `src/graph.py` is fully modular. To add a new reasoning step (e.g., `Verify_Fix` after `Execute_Skill`):
1. Define a new node function: `async def verify_fix(state: State) -> Command[Literal["..."]]:`
2. Register the node in the graph builder: `builder.add_node("Verify_Fix", verify_fix)`
3. Update the edge transitions, mapping the existing `Execute_Skill` exit path to route to `Verify_Fix`.
