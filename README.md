# Autonomous SRE Agent

An elite, production-ready open-source Autonomous SRE (Site Reliability Engineering) Agent. 
It intercepts infrastructure incident payloads, analyzes them using a local open-source LLM via Ollama, uses CockroachDB Cloud for memory and persistence, and executes remediation actions.

## Prerequisites

- Python 3.11+
- CockroachDB Cloud Cluster
- Ollama installed locally or accessible remotely

## Setup

1. Configure your environment variables by copying the template:
   ```bash
   cp .env.template .env
   ```
   Edit `.env` and fill in `COCKROACH_DATABASE_URL`. `OLLAMA_BASE_URL` is optional and defaults to `http://localhost:11434`.

2. **MANDATORY**: Pull the required local models via Ollama before running the agent:
   ```bash
   ollama pull nomic-embed-text
   ollama pull qwen2.5-coder:7b-instruct
   ```

3. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. Start the main agent listener:
   ```bash
   python src/main.py
   ```
   This will initialize the database tables, perform the model fallback check, and wait for incoming incidents.

2. In a separate terminal, trigger a mock incident:
   ```bash
   python scripts/mock_alerts.py
   ```
   Follow the prompts to send an alert to the agent and watch it analyze and resolve the issue.

## Architecture

- **LangGraph**: Orchestrates the state machine for the agent's workflow.
- **CockroachDB**: Stores historical incidents with vector embeddings (`pgvector`) and acts as the LangGraph state checkpointer via `AsyncPostgresSaver`.
- **Ollama**: Provides local LLM inference with an automated fallback cascade (e.g. `gemma4:26b` -> `gemma4:12b` -> `qwen2.5-coder:32b-instruct` -> `qwen2.5-coder:7b-instruct`).
