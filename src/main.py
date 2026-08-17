import asyncio
import sys
import json
import os
import uuid
import traceback
import paho.mqtt.client as mqtt

from dotenv import load_dotenv
load_dotenv()

import psycopg_pool
from langgraph.checkpoint.memory import MemorySaver

from functools import partial
from src.database import init_db, get_db_uri, route_to_dlq
from src.graph import build_graph, AgentState
from src.tools import validate_mcp_config
from src.llm_factory import aget_active_llm
from src.metrics import INCIDENT_COUNTER
from src.secrets_manager import load_secrets
from src.slack_bot import start_slack_bot, send_hitl_alert, hitl_futures

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_compiled_graph = None
_init_lock = None
_cli_lock = None

def get_init_lock():
    global _init_lock
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    return _init_lock

def get_cli_lock():
    global _cli_lock
    if _cli_lock is None:
        _cli_lock = asyncio.Lock()
    return _cli_lock

def _log_future_error(payload, loop, f):
    """Callback for run_coroutine_threadsafe futures — logs exceptions and routes to DLQ if task fails."""
    try:
        exc = f.exception()
        if exc:
            print(f"ASYNC ERROR in incident processing: {exc}")
            asyncio.run_coroutine_threadsafe(route_to_dlq(payload, str(exc)), loop)
    except asyncio.CancelledError:
        print("ASYNC ERROR: Incident processing was cancelled.")
        asyncio.run_coroutine_threadsafe(route_to_dlq(payload, "Incident processing was cancelled"), loop)
    except Exception as e:
        print(f"ASYNC ERROR in callback: {e}")


async def init_global_graph():
    global _compiled_graph
    if _compiled_graph:
        return _compiled_graph

    lock = get_init_lock()
    async with lock:
        # Double-check after acquiring lock
        if _compiled_graph:
            return _compiled_graph

        graph_builder = build_graph()
        
        # Switched to MemorySaver to bypass the CockroachDB jsonb_each_text error
        checkpointer = MemorySaver()
        _compiled_graph = graph_builder.compile(checkpointer=checkpointer, interrupt_before=["Execute_Skill"])

        return _compiled_graph


async def process_incident(payload: dict) -> None:
    try:
        graph = await init_global_graph()
        await run_graph(graph, payload)
    except Exception as e:
        print(f"CRITICAL ERROR: Workflow crashed. Routing payload to DLQ.")
        traceback.print_exc()
        await route_to_dlq(payload, str(e))


async def run_graph(graph, payload) -> None:
    initial_state = AgentState(
        incident_id=payload.get("id", str(uuid.uuid4())),
        alert_type=payload.get("alert_type", ""),
        raw_logs=payload.get("raw_logs", "")
    )

    config = {"configurable": {"thread_id": initial_state.incident_id}}

    print(f"\n--- Starting SRE Workflow for Incident {initial_state.incident_id} ---")

    async for event in graph.astream(initial_state, config=config):
        pass  # Logs are handled inside the nodes

    # Check for HITL interrupt
    state = await graph.aget_state(config)
    if state.next and "Execute_Skill" in state.next:
        action_args = state.values.get("action_args", {})
        if action_args.get("write_consent"):
            cli_lock = get_cli_lock()
            async with cli_lock:
                print(f"\n[HITL GATE] Incident {initial_state.incident_id} requests destructive action:")
                print(f"Tool: {state.values.get('proposed_action')}")
                print(f"Args: {action_args}")
                
                # Create a future to listen for Slack responses
                slack_future = asyncio.get_running_loop().create_future()
                hitl_futures[initial_state.incident_id] = slack_future
                
                # Dispatch the interactive alert via the Slack Bot
                await send_hitl_alert(initial_state.incident_id, state.values.get('proposed_action'), action_args)
                
                print(" -> WARNING: Execution paused for 120s pending operator approval (Slack or Terminal).")
                
                response = None
                
                # Task 1: Terminal Input (Interactive)
                async def wait_for_terminal():
                    if not sys.stdin.isatty():
                        print(" -> Non-interactive environment detected. Terminal input disabled.")
                        # Wait forever if non-interactive, so Slack can take over
                        await asyncio.sleep(86400)
                        return "n"
                    
                    print("Approve execution? [y/N]: ", end="", flush=True)
                    if sys.platform == 'win32':
                        import msvcrt
                        while True:
                            if await asyncio.to_thread(msvcrt.kbhit):
                                char = await asyncio.to_thread(msvcrt.getche)
                                if char in (b'\r', b'\n'):
                                    print()
                                    return "n"
                                return char.decode('utf-8', 'ignore')
                            await asyncio.sleep(0.1)
                    else:
                        # Use asyncio.to_thread for sys.stdin.readline
                        ans = await asyncio.to_thread(sys.stdin.readline)
                        return ans.strip()

                terminal_task = asyncio.create_task(wait_for_terminal())
                slack_task = asyncio.create_task(slack_future)
                
                try:
                    # Race the terminal input vs the Slack response vs the 120s timeout
                    done, pending = await asyncio.wait(
                        [terminal_task, slack_task],
                        timeout=120.0,
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    if not done:
                        print("\n -> Timeout (120s) waiting for operator approval.")
                        response = "n"
                    else:
                        first_completed = done.pop()
                        response = first_completed.result()
                        if first_completed == slack_task:
                            print(f"\n -> Approval received from Slack: {response}")
                        else:
                            print(f"\n -> Approval received from Terminal: {response}")
                            
                except Exception as e:
                    print(f"\n -> Error during HITL wait: {e}")
                    response = "n"
                finally:
                    # Cleanup
                    terminal_task.cancel()
                    slack_task.cancel()
                    if initial_state.incident_id in hitl_futures:
                        del hitl_futures[initial_state.incident_id]
                
            if response.strip().lower() in ('y', 'yes'):
                print(" -> Execution approved by operator.")
                async for event in graph.astream(None, config=config):
                    pass
            else:
                print(" -> Execution DENIED by operator. Aborting.")
                await graph.aupdate_state(config, {"execution_status": "rejected"})
                # Explicitly resume the graph so it can process the rejection and terminate cleanly
                async for event in graph.astream(None, config=config):
                    pass
        else:
            # Not a destructive action, auto-resume
            async for event in graph.astream(None, config=config):
                pass

    state_dict = await graph.aget_state(config)
    final_status = state_dict.values.get("execution_status", "unknown")
    alert_type = state_dict.values.get("alert_type", "Unknown Edge Anomaly")
    
    # Record metrics
    INCIDENT_COUNTER.labels(
        alert_type=alert_type,
        resolution_status=final_status
    ).inc()

    print(f"\n--- Workflow Complete for Incident {initial_state.incident_id} ---")

def on_message(client, userdata, msg):
    try:
        payload_str = msg.payload.decode("utf-8")
        payload = json.loads(payload_str)

        if not isinstance(payload, dict):
            print("Error: Telemetry payload is not a valid JSON dictionary.")
            return

        loop = userdata['loop']
        future = asyncio.run_coroutine_threadsafe(process_incident(payload), loop)
        
        callback = partial(_log_future_error, payload, loop)
        future.add_done_callback(callback)
    except Exception as e:
        print(f"Error parsing ESP32 telemetry: {e}")
        traceback.print_exc()


async def run_mqtt_listener():
    print("Initializing MQTT Listener for ESP32 Telematics...")
    
    # Pre-warm LLM — bounded timeout so a slow LiteLLM gateway at boot does
    # NOT stall the MQTT listener from starting.
    try:
        await asyncio.wait_for(aget_active_llm(), timeout=30.0)
    except asyncio.TimeoutError:
        print("LLM warm-up timed out after 30s. Continuing; will retry on first incident.")
    except Exception as e:
        print(f"Could not lock in an LLM at startup: {e}")
        print("Continuing anyway; will retry during incident processing.")
        
    loop = asyncio.get_running_loop()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, userdata={'loop': loop})
    
    # CRITICAL: Define on_connect to guarantee subscription persistence across reconnects
    def on_connect(client, userdata, flags, reason_code, properties):
        print(f"Connected to MQTT Broker with result code {reason_code}. Subscribing to telemetry...")
        client.subscribe("sre/edge/telemetry")

    client.on_connect = on_connect
    client.on_message = on_message
    
    # CRITICAL: client.connect() is a synchronous blocking TCP call. Offload to
    # a thread so the event loop is not stalled during the broker handshake.
    mqtt_host = os.getenv("MQTT_BROKER_HOST", "localhost")
    client.tls_set(ca_certs="certs/ca.crt")
    await asyncio.to_thread(client.connect, mqtt_host, 8883, 60)
    client.loop_start()
    print("Listening for live hardware anomalies...")
    try:
        stop_event = asyncio.Event()
        await stop_event.wait()
    finally:
        print("Shutting down gracefully...")
        client.loop_stop()
        client.disconnect()
        try:
            from src.database import get_pool
            pool = get_pool()
            await pool.close()
            print("Database connection pool closed.")
        except RuntimeError:
            pass


def start_metrics_server():
    from prometheus_client import start_http_server
    print("Starting Prometheus metrics server on port 8000...")
    start_http_server(8000)

if __name__ == "__main__":
    print("Initializing Autonomous SRE Agent...")
    
    async def main():
        load_secrets()
        
        # Start Slack Socket Mode Bot in the background
        asyncio.create_task(start_slack_bot())
        
        await init_db()
        validate_mcp_config()
        start_metrics_server()
        await run_mqtt_listener()
        
    asyncio.run(main())
