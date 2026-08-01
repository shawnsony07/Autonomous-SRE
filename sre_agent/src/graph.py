import json
import os
import re

import psycopg_pool
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Dict

from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, START, END

from src.database import get_db_uri, _format_vector
from src.llm_factory import aget_active_llm, get_embeddings
from src.tools import run_mcp_tool
from src.prompts import DETECT_INGEST_PROMPT, REASON_PLAN_SYSTEM_PROMPT, REASON_PLAN_HUMAN_PROMPT

# Define strict AgentState schema using Pydantic
class AgentState(BaseModel):
    model_config = ConfigDict(frozen=False)

    incident_id: str
    alert_type: str = ""
    raw_logs: str = ""
    historical_context: str = ""
    proposed_action: str = ""
    action_args: Dict[str, Any] = Field(default_factory=dict)
    execution_status: str = "pending"
    retry_count: int = 0


async def detect_ingest_node(state: AgentState) -> AgentState:
    print(f"\n[Node: Detect/Ingest] Processing incident {state.incident_id}")
    cleaned_logs = state.raw_logs.strip()

    llm = await aget_active_llm()
    prompt = DETECT_INGEST_PROMPT.format(cleaned_logs=cleaned_logs)

    try:
        response = await llm.ainvoke(prompt)
        raw_content = response.content.strip()
        content = {}
        end = raw_content.rfind('}')
        if end != -1:
            for start in range(raw_content.find('{'), end):
                if raw_content[start] == '{':
                    try:
                        content = json.loads(raw_content[start:end+1])
                        break
                    except json.JSONDecodeError:
                        continue

        alert_type = content.get("alert_type", "Unknown Edge Anomaly")
        if not alert_type and "Unknown Edge Anomaly" not in content:
            alert_type = "Unknown Edge Anomaly"
    except Exception as e:
        print(f" -> LLM Detection failed: {e}")
        alert_type = "Unknown Edge Anomaly"
        
    return {"alert_type": alert_type, "raw_logs": cleaned_logs}


async def retrieve_memory_node(state: AgentState) -> AgentState:
    print(f"\n[Node: Retrieve_Memory] Searching history for '{state.alert_type}'")
    db_uri = get_db_uri()

    embedder = get_embeddings()
    query_vector = _format_vector(await embedder.aembed_query(state.raw_logs))

    hist = ""
    try:
        from src.main import _global_pool
        async with _global_pool.connection() as conn:
            async with conn.cursor() as cur:
                # Use CTE to avoid sending the vector parameter twice
                await cur.execute("""
                    WITH q AS (SELECT %s::vector AS v)
                    SELECT error_log, resolution_steps
                    FROM incident_memory, q
                    WHERE embedding <=> q.v < 0.25
                    ORDER BY embedding <=> q.v
                    LIMIT 1
                """, (query_vector,))

                row = await cur.fetchone()

                if row:
                    hist = f"Similar Incident Log: {row[0]}\nResolution Used: {row[1]}"
                    print(f" -> Found historical match: {row[1]}")
                else:
                    hist = "No relevant historical incidents found."
                    print(" -> No historical matches found.")

    except Exception as e:
        print(f" -> Database retrieval error: {e}")
        hist = "Error retrieving historical context."

    return {"historical_context": hist}


async def reason_plan_node(state: AgentState) -> AgentState:
    print(f"\n[Node: Reason_Plan] Planning remediation for '{state.alert_type}'")
    llm = await aget_active_llm()

    sys_msg = SystemMessage(content=REASON_PLAN_SYSTEM_PROMPT)
    hum_msg = HumanMessage(content=REASON_PLAN_HUMAN_PROMPT.format(
        alert_type=state.alert_type,
        raw_logs=state.raw_logs,
        historical_context=state.historical_context
    ))

    try:
        print(" -> Invoking Ollama LLM (this may take a few minutes for a 12B parameter model... please wait)")
        response = await llm.ainvoke([sys_msg, hum_msg])

        raw_content = response.content.strip()
        content = {}
        end = raw_content.rfind('}')
        if end != -1:
            for start in range(raw_content.find('{'), end):
                if raw_content[start] == '{':
                    try:
                        content = json.loads(raw_content[start:end+1])
                        break
                    except json.JSONDecodeError:
                        continue
                        
        proposed_action = content.get("tool_name", "")
        action_args = content.get("arguments", {})
        print(f" -> Plan created: {proposed_action} with args {action_args}")
        return {"proposed_action": proposed_action, "action_args": action_args}
    except Exception as e:
        print(f" -> LLM Planning failed: {e}")
        return {"execution_status": "failed_planning"}


async def execute_skill_node(state: AgentState) -> AgentState:
    print(f"\n[Node: Execute_Skill] Executing action '{state.proposed_action}'")
    if state.execution_status == "rejected":
        print(" -> Execution aborted by HITL gate. Bypassing MCP.")
        return {"execution_status": "rejected"}

    if state.execution_status == "failed_planning":
        print(" -> Skipping execution due to planning failure.")
        return {"execution_status": "failed_planning"}

    action = state.proposed_action
    args = state.action_args

    if not action:
        print(" -> No action proposed. Skipping execution.")
        return {"execution_status": "failed_planning"}

    try:
        result = await run_mcp_tool(action, args)
        print(f" -> Execution Result: {result}")
        return {"execution_status": "resolved"}
    except Exception as e:
        print(f" -> Execution Failed: {e}")
        return {"execution_status": "failed_execution", "retry_count": state.retry_count + 1}


def should_retry(state: AgentState) -> str:
    if state.execution_status == "failed_execution" and state.retry_count < 3:
        print(f" -> Retrying failed execution. Attempt {state.retry_count}/3.")
        return "Reason_Plan"
    return END

def build_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("Detect_Ingest", detect_ingest_node)
    workflow.add_node("Retrieve_Memory", retrieve_memory_node)
    workflow.add_node("Reason_Plan", reason_plan_node)
    workflow.add_node("Execute_Skill", execute_skill_node)

    workflow.add_edge(START, "Detect_Ingest")
    workflow.add_edge("Detect_Ingest", "Retrieve_Memory")
    workflow.add_edge("Retrieve_Memory", "Reason_Plan")
    workflow.add_edge("Reason_Plan", "Execute_Skill")
    workflow.add_conditional_edges("Execute_Skill", should_retry, {"Reason_Plan": "Reason_Plan", END: END})

    return workflow
