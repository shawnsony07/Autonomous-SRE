import os
import asyncio
from mcp.client.session import ClientSession
import paho.mqtt.client as mqtt
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

try:
    from mcp.client.http import http_client  # type: ignore
except ImportError:
    try:
        from mcp.client.streamable_http import streamablehttp_client as http_client  # type: ignore
    except ImportError:
        from mcp.client.streamable_http import streamable_http_client as http_client

COCKROACH_MCP_URL = os.environ.get("COCKROACH_MCP_URL", "https://mock-mcp-server.cockroachlabs.cloud/sse")
COCKROACH_MCP_API_KEY = os.environ.get("COCKROACH_MCP_API_KEY", "")
MCP_CLUSTER_ID = os.environ.get("MCP_CLUSTER_ID", "")

# Allowlist of safe edge commands to prevent LLM hallucination escalation
import threading
import atexit

_DEFAULT_EDGE_COMMANDS = "FAN_ON,FAN_OFF,RELAY_TRIGGER,RELAY_RESET,DEEP_SLEEP,WAKE,THROTTLE_CPU,RESET_I2C"
_env_commands = os.environ.get("ALLOWED_EDGE_COMMANDS", _DEFAULT_EDGE_COMMANDS)
ALLOWED_EDGE_COMMANDS = {cmd.strip() for cmd in _env_commands.split(",") if cmd.strip()}

_mqtt_publisher = None
_mqtt_lock = threading.Lock()

def _shutdown_mqtt():
    global _mqtt_publisher
    with _mqtt_lock:
        if _mqtt_publisher is not None:
            _mqtt_publisher.loop_stop()
            _mqtt_publisher.disconnect()
            _mqtt_publisher = None

atexit.register(_shutdown_mqtt)

def get_mqtt_publisher() -> mqtt.Client:
    global _mqtt_publisher
    with _mqtt_lock:
        if _mqtt_publisher is None:
            _mqtt_publisher = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)  # type: ignore
            _mqtt_publisher.tls_set(ca_certs="certs/ca.crt")
            _mqtt_publisher.connect("localhost", 8883, 60)
            _mqtt_publisher.loop_start()
        return _mqtt_publisher

def validate_mcp_config():
    """Validate that required MCP configuration is present. Call at startup."""
    missing = []
    if not COCKROACH_MCP_API_KEY:
        missing.append("COCKROACH_MCP_API_KEY")
    if not MCP_CLUSTER_ID:
        missing.append("MCP_CLUSTER_ID")
    if missing:
        print(f"WARNING: Missing MCP environment variables: {', '.join(missing)}. MCP tool calls will fail.")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
async def publish_edge_command(command: str) -> str:
    if not command:
        return "Error: Empty command string provided."

    if command not in ALLOWED_EDGE_COMMANDS:
        return f"Error: Command '{command}' is not in the approved allowlist. Allowed: {sorted(ALLOWED_EDGE_COMMANDS)}"

    print(f"[EDGE COMMAND] Dispatching: {command}")
    
    # Offload the blocking MQTT connect and publish operations to a background thread
    def _sync_publish():
        client = get_mqtt_publisher()
        result = client.publish("sre/edge/commands", command)
        result.wait_for_publish(timeout=5.0)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"MQTT publish failed with rc {result.rc}")
        
    await asyncio.to_thread(_sync_publish)
    return f"Successfully published edge command: {command}"

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), retry=retry_if_exception_type((ConnectionError, asyncio.TimeoutError, httpx.RequestError)))
async def _execute_mcp_call(tool_name: str, safe_args: dict, headers: dict) -> str:
    import inspect
    from contextlib import AsyncExitStack
    
    # Use HTTP transport rather than SSE to fix HTTP 405 error
    async with AsyncExitStack() as stack:
        sig = inspect.signature(http_client)
        if "http_client" in sig.parameters:
            client = await stack.enter_async_context(httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(5.0, read=300.0)))
            streams = await stack.enter_async_context(http_client(COCKROACH_MCP_URL, http_client=client))
        else:
            streams = await stack.enter_async_context(http_client(COCKROACH_MCP_URL, headers=headers))
            
        if len(streams) == 3:
            read_stream, write_stream, _ = streams
        else:
            read_stream, write_stream = streams[:2]
            
        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        result = await session.call_tool(tool_name, arguments=safe_args)
        
        if hasattr(result, 'content') and result.content and len(result.content) > 0:
            output = getattr(result.content[0], 'text', str(result.content[0]))
            return f"Successfully executed {tool_name}. Result: {output}"
        else:
            return f"Successfully executed {tool_name}. No content returned."

async def run_mcp_tool(tool_name: str, arguments: dict) -> str:
    safe_args = arguments or {}

    if not tool_name:
        return "Error: No tool_name was provided for execution."

    if tool_name == "publish_edge_command":
        try:
            return await publish_edge_command(safe_args.get("command", ""))
        except Exception as e:
            return f"Failed to publish edge command. Error: {e}"

    print(f"[TOOL] Executing MCP tool: {tool_name} with args: {safe_args}")

    headers = {
        "Authorization": f"Bearer {COCKROACH_MCP_API_KEY}",
        "mcp-cluster-id": MCP_CLUSTER_ID
    }

    try:
        return await _execute_mcp_call(tool_name, safe_args, headers)
    except Exception as e:
        error_msg = f"Failed to execute MCP tool {tool_name}. Error: {e}"
        print(f"[TOOL] {error_msg}")
        raise RuntimeError(error_msg)
