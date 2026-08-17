import asyncio
import os
from contextlib import AsyncExitStack

from src.tools import COCKROACH_MCP_URL, COCKROACH_MCP_API_KEY, MCP_CLUSTER_ID, http_client
from mcp.client.session import ClientSession

async def main():
    headers = {
        "Authorization": f"Bearer {COCKROACH_MCP_API_KEY}",
        "mcp-cluster-id": MCP_CLUSTER_ID
    }
    print(f"URL: {COCKROACH_MCP_URL}")
    import inspect
    import httpx
    async with AsyncExitStack() as stack:
        sig = inspect.signature(http_client)
        if "http_client" in sig.parameters:
            client = await stack.enter_async_context(httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(5.0, read=30.0)))
            streams = await stack.enter_async_context(http_client(COCKROACH_MCP_URL, http_client=client))
        else:
            streams = await stack.enter_async_context(http_client(COCKROACH_MCP_URL, headers=headers))
            
        if len(streams) == 3:
            read_stream, write_stream, _ = streams
        else:
            read_stream, write_stream = streams[:2]
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        tools = await session.list_tools()
        print("AVAILABLE TOOLS:")
        for t in tools.tools:
            print(f"- {t.name}: {t.description}")

asyncio.run(main())
