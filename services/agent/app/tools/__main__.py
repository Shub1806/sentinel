"""Run a tool by hand, from inside the agent pod:

python -m app.tools list
python -m app.tools run k8s_pods '{}'
python -m app.tools run prometheus_query '{"query": "up{namespace=\\"sentinel\\"}"}'
"""

import asyncio
import json
import sys

import httpx2

from app.config import load_settings
from app.tools.base import call_tool
from app.tools.registry import build_tools


async def main(argv: list[str]) -> int:
    settings = load_settings()
    async with httpx2.AsyncClient(timeout=settings.tool_timeout_seconds) as client:
        tools = build_tools(settings, client)
        if argv[:1] == ["list"]:
            for tool in tools.values():
                print(f"{tool.name}: {tool.description}")
            return 0
        if len(argv) in (2, 3) and argv[0] == "run":
            args = json.loads(argv[2]) if len(argv) == 3 else {}
            print(await call_tool(tools, argv[1], args, settings.tool_max_chars))
            return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
