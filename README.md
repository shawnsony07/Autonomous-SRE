# EdgeOps Agent: Cognitive SRE with Distributed Vector Memory

![EdgeOps Agent Logo](logo.png)
## Overview
The Autonomous SRE Agent is a next-generation reliability system designed to autonomously ingest, diagnose, and remediate hardware and software anomalies in real time. It leverages a tiered Large Language Model (LLM) architecture via LiteLLM, persistent vector memory backed by CockroachDB Serverless, and direct edge-device interaction through MQTT. 

When anomalies are detected, the agent uses LangGraph to orchestrate a reasoning pipeline that mimics an expert Site Reliability Engineer (SRE). It retrieves historical incidents, synthesizes root cause analyses, and executes remediation tools via the Model Context Protocol (MCP), all while enforcing a strict Human-in-the-Loop (HITL) gate for destructive operations.

## Architecture

```mermaid
flowchart TD
    %% Styling
    classDef edge fill:#0288d1,stroke:#01579b,stroke-width:3px,color:#fff,font-weight:bold
    classDef agent fill:#7b1fa2,stroke:#4a0072,stroke-width:3px,color:#fff,font-weight:bold
    classDef service fill:#2e7d32,stroke:#1b5e20,stroke-width:3px,color:#fff,font-weight:bold
    classDef danger fill:#c62828,stroke:#7f0000,stroke-width:3px,color:#fff,font-weight:bold
    classDef db fill:#e65100,stroke:#bf360c,stroke-width:3px,color:#fff,font-weight:bold
    classDef llm fill:#4527a0,stroke:#1a0072,stroke-width:3px,color:#fff,font-weight:bold

    %% Define Edge & Infrastructure
    ESP32["Edge Device / ESP32"]:::edge -- Telemetry & Anomalies --> MQTT["Mosquitto MQTT Broker"]:::service
    
    %% SRE Agent Core
    subgraph SRE Agent
        MQTT_Listener["MQTT Async Listener"]:::agent
        LangGraph["LangGraph State Workflow"]:::agent
        HITL["Human-In-The-Loop Gate"]:::danger
    end
    
    MQTT -- Trigger Workflow --> MQTT_Listener
    MQTT_Listener --> LangGraph
    
    %% External Services
    LiteLLM["LiteLLM Gateway"]:::llm
    CockroachDB[("CockroachDB Serverless")]:::db
    MCP["CockroachDB Managed MCP"]:::service

    %% Flow interactions
    LangGraph -- 1. Vector Search --> CockroachDB
    LangGraph -- 2. Anomaly Detection & Reasoning --> LiteLLM
    LangGraph -- 3. Propose Action --> HITL
    
    HITL -- Approve --> MCP
    MCP -- Execute Infrastructure Change --> System["Target Infrastructure / DB"]:::db
    
    HITL -- Deny --> Abort(("Abort")):::danger
    
    %% LLM Tiers
    subgraph LLM Routing
        LiteLLM --> FastTier["sre-fast-tier (Flash Lite)"]:::llm
        LiteLLM --> ComplexTier["sre-complex-tier (Flash)"]:::llm
        LiteLLM -.-> Ollama["Fallback Local Ollama"]:::llm
    end
```

## Core Technical Components

### 1. LangGraph Workflow (The Brain)
The heart of the SRE Agent is a deterministic state machine built with LangGraph. It processes incidents through a strictly defined execution graph:

```mermaid
stateDiagram-v2
    classDef process fill:#1565c0,stroke:#003c8f,stroke-width:3px,color:#fff,font-weight:bold
    classDef danger fill:#b71c1c,stroke:#7f0000,stroke-width:3px,color:#fff,font-weight:bold
    classDef success fill:#1b5e20,stroke:#003300,stroke-width:3px,color:#fff,font-weight:bold

    [*] --> Detect_Ingest
    Detect_Ingest --> Retrieve_Memory: Parse Telemetry
    Retrieve_Memory --> Reason_Plan: Fetch Vector History
    Reason_Plan --> Execute_Skill: Synthesize Remediation
    
    state Execute_Skill {
        [*] --> HITL_Gate
        HITL_Gate --> ExecTool : User Approves
        HITL_Gate --> Abort : User Denies
        ExecTool --> [*]
        Abort --> [*]
    }
    
    Execute_Skill --> Reason_Plan: Tool Failed (Retry < 3)
    Execute_Skill --> [*]: Resolved / Aborted

    class Detect_Ingest, Retrieve_Memory, Reason_Plan process
    class Execute_Skill process
    class HITL_Gate, Abort danger
    class ExecTool success
```

### 2. Tiered LLM Routing (LiteLLM)
To balance latency, cost, and intelligence, the agent routes requests through a LiteLLM gateway using specific tier aliases:
- **`sre-fast-tier`**: High-frequency, low-latency tasks (e.g., initial log parsing, anomaly categorization) using fast, lightweight models.
- **`sre-complex-tier`**: Deep reasoning tasks (e.g., root cause analysis, script generation) using advanced models.
- **Local Fallback**: An integrated Ollama fallback handles embeddings (e.g., `nomic-embed-text`) and acts as a safety net if external API limits are exceeded.

### 3. Vector Memory (CockroachDB)
The agent stores and recalls past incidents using a `pgvector` enabled schema on CockroachDB Serverless.
- **768-Dimension Embeddings**: Text logs and resolution steps are embedded to support semantic search.
- **Distributed Vector Indexing**: Utilizes CockroachDB's `CREATE VECTOR INDEX` for high-performance approximate nearest neighbor (ANN) search, injecting highly relevant historical context into the prompt before the reasoning phase.

### 4. Edge Telemetry (MQTT)
Real-time telemetry from edge hardware (like ESP32 thermistors or voltage sensors) is ingested via `paho.mqtt`. 
- **Non-blocking Architecture**: Network I/O and connection routines are executed inside `asyncio.to_thread` wrappers to prevent stalling the Python `asyncio` event loop.

### 5. Action Execution (MCP)
The agent executes infrastructure and database changes securely via the **Model Context Protocol (MCP)**.
- Integrates with the CockroachDB Managed MCP endpoint.
- Uses HTTP Transport with dynamic stream handling, overcoming standard SSE protocol limitations.

### 6. Human-In-The-Loop (HITL) Gate
Destructive or configuration-altering actions trigger an automatic pause in the LangGraph workflow.
- Operator approval is requested via the interactive terminal.
- An async polling mechanism (using `sys.stdin.readline` or `msvcrt`) waits for 30 seconds.
- Non-interactive environments are auto-denied to ensure safety.

## Codebase Structure

```text
Autonomous SRE/
├── docker-compose.yml       # Orchestrates the SRE Agent, Mosquitto, and LiteLLM
├── litellm_config.yaml      # Configures the tiered routing and RPM quotas
├── mosquitto.conf           # MQTT Broker configuration
├── .env                     # Secrets and endpoint configurations
└── src/                     # Core Agent Source Code
    ├── main.py              # Entry point: MQTT listener, LangGraph execution, HITL
    ├── graph.py             # LangGraph state machine definition and node logic
    ├── database.py          # CockroachDB connection pooling, schema setup, and vector indexing
    ├── llm_factory.py       # LiteLLM routing, async initialization, and caching
    ├── tools.py             # MCP execution, MQTT publishing, and HTTP transport fixes
    └── prompts.py           # System prompts for detection and planning
```

## Setup & Configuration

### Prerequisites
- Docker & Docker Compose
- A CockroachDB Serverless Cluster (with `pgvector` extension manually enabled by a superuser)
- Google Gemini API Key (or alternative models configured in `litellm_config.yaml`)

### 1. Environment Variables
Create a `.env` file based on `.env.template`:
```env
GEMINI_API_KEY=your_google_api_key
COCKROACH_DATABASE_URL=postgresql://user:password@host:26257/defaultdb?sslmode=verify-full
COCKROACH_MCP_URL=https://mock-mcp-server.cockroachlabs.cloud/sse
COCKROACH_MCP_API_KEY=your_mcp_api_key
MCP_CLUSTER_ID=your_cluster_id
```

### 2. Launch the System
Start the entire stack using Docker Compose:
```bash
docker compose up --build
```
This command spins up the local MQTT broker, the LiteLLM gateway, and the SRE Agent itself. The agent will automatically initialize the database schema, warm up the LLM connections, and begin listening for telemetry.

## Extending the Agent

### Adding New MCP Tools
To expand the agent's capabilities (e.g., integrating with AWS, Datadog, or Slack), you can add new MCP tool schemas in `src/tools.py`.
1. Define the tool wrapper using `@tool`.
2. Connect to the respective MCP server using the `mcp.client.session.ClientSession`.
3. Add the tool to the `tools` array exposed to the LLM during the `Reason_Plan` node.

### Extending the LangGraph Workflow
The state machine in `src/graph.py` is fully modular. To add a new reasoning step (e.g., `Verify_Fix` after `Execute_Skill`):
1. Define a new node function: `async def verify_fix(state: State) -> Command[Literal["..."]]:`
2. Register the node in the graph builder: `builder.add_node("Verify_Fix", verify_fix)`
3. Update the edge transitions, changing the existing `Execute_Skill` exit path to route to `Verify_Fix`.
