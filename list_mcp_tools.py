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
    async with AsyncExitStack() as stack:
        read_stream, write_stream = await stack.enter_async_context(
            http_client(url=COCKROACH_MCP_URL, headers=headers)
        )
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await session.initialize()
        tools = await session.list_tools()
        print("AVAILABLE TOOLS:")
        for t in tools.tools:
            print(f"- {t.name}: {t.description}")

asyncio.run(main())
