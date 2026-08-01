import asyncio
import sys
import json
import uuid
import traceback
import paho.mqtt.client as mqtt

from dotenv import load_dotenv
load_dotenv()

import psycopg_pool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from src.database import init_db, get_db_uri
from src.graph import build_graph, AgentState
from src.tools import validate_mcp_config
from src.llm_factory import aget_active_llm

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_global_pool = None
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

def _log_future_error(f):
    """Callback for run_coroutine_threadsafe futures — logs exceptions without crashing the MQTT thread."""
    try:
        exc = f.exception()
        if exc:
            print(f"ASYNC ERROR in incident processing: {exc}")
    except asyncio.CancelledError:
        print("ASYNC ERROR: Incident processing was cancelled.")
    except Exception as e:
        print(f"ASYNC ERROR in callback: {e}")


async def init_global_graph():
    global _global_pool, _compiled_graph
    if _compiled_graph:
        return _compiled_graph

    lock = get_init_lock()
    async with lock:
        # Double-check after acquiring lock
        if _compiled_graph:
            return _compiled_graph

        graph_builder = build_graph()
        db_uri = get_db_uri()

        # CRITICAL FIX: Pass open=False so we can manually await _global_pool.open() without crashing
        _global_pool = psycopg_pool.AsyncConnectionPool(db_uri, open=False)
        await _global_pool.open()
        
        # CRITICAL FIX: Use the official Postgres checkpointer
        checkpointer = AsyncPostgresSaver(_global_pool)
        await checkpointer.setup()
        _compiled_graph = graph_builder.compile(checkpointer=checkpointer, interrupt_before=["Execute_Skill"])

        return _compiled_graph


async def process_incident(payload: dict) -> None:
    try:
        graph = await init_global_graph()
        await run_graph(graph, payload)
    except Exception as e:
        print(f"CRITICAL ERROR in workflow execution: {e}")
        traceback.print_exc()


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
                
                print(" -> WARNING: Execution paused for 30s pending operator approval.")
                if not sys.stdin.isatty():
                    print(" -> Non-interactive environment detected. Auto-denying.")
                    response = "n"
                else:
                    print("Approve execution? [y/N]: ", end="", flush=True)
                    if sys.platform == 'win32':
                        import msvcrt, time
                        start_time = time.time()
                        response = "n"
                        while time.time() - start_time < 30:
                            if msvcrt.kbhit():
                                char = msvcrt.getche()
                                if char in (b'\r', b'\n'):
                                    print()
                                    break
                                response = char.decode('utf-8', 'ignore')
                            await asyncio.sleep(0.1)
                    else:
                        import select
                        r, _, _ = select.select([sys.stdin], [], [], 30.0)
                        if r:
                            response = sys.stdin.readline().strip()
                        else:
                            print("\n -> Timeout waiting for operator approval.")
                            response = "n"
                
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
        future.add_done_callback(_log_future_error)
    except Exception as e:
        print(f"Error parsing ESP32 telemetry: {e}")
        traceback.print_exc()


async def run_mqtt_listener():
    print("Initializing MQTT Listener for ESP32 Telematics...")
    
    # Pre-warm LLM checking logic at startup, within the main event loop
    try:
        await aget_active_llm()
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
    
    client.connect("localhost", 1883, 60)
    # Remove the imperative client.subscribe() call here
    client.loop_start()
    print("Listening for live hardware anomalies...")
    try:
        stop_event = asyncio.Event()
        await stop_event.wait()
    finally:
        print("Shutting down gracefully...")
        client.loop_stop()
        client.disconnect()
        if _global_pool:
            await _global_pool.close()
            print("Database connection pool closed.")


if __name__ == "__main__":
    print("Initializing Autonomous SRE Agent...")
    
    async def main():
        await init_db()
        validate_mcp_config()
        await run_mqtt_listener()
        
    asyncio.run(main())
