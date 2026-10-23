"""Loki tool: search the logs of the watched namespace."""

from datetime import UTC, datetime, timedelta

import httpx2
from pydantic import BaseModel, Field

from app.tools.base import Tool, ToolError

MAX_LINE_CHARS = 400


class LogParams(BaseModel):
    query: str = Field(
        description='LogQL query, for example: {namespace="sentinel", app="payments"} |= "ERROR"'
    )
    minutes: int = Field(default=15, ge=1, le=360, description="How far back to search")
    limit: int = Field(default=30, ge=1, le=200, description="Most lines to return, newest first")


def loki_tools(
    client: httpx2.AsyncClient, base_url: str, namespace: str, clock=lambda: datetime.now(UTC)
):
    async def logs(p: LogParams) -> dict:
        # Only the watched namespace: its logs are what the agent is allowed to read.
        if f'namespace="{namespace}"' not in p.query:
            raise ToolError(f'the query must select namespace="{namespace}"')
        end = clock()
        start = end - timedelta(minutes=p.minutes)
        response = await client.get(
            f"{base_url}/loki/api/v1/query_range",
            params={
                "query": p.query,
                "start": int(start.timestamp() * 1e9),
                "end": int(end.timestamp() * 1e9),
                "limit": p.limit,
                "direction": "backward",
            },
        )
        if response.status_code != 200:
            raise ToolError(f"Loki said {response.status_code}: {response.text[:300]}")
        lines = []
        for stream in response.json()["data"]["result"]:
            labels = stream["stream"]
            source = labels.get("pod") or labels.get("app", "?")
            for ts, line in stream["values"]:
                lines.append((int(ts), source, line))
        lines.sort(reverse=True)
        return {
            "lines": [
                {
                    "time": datetime.fromtimestamp(ts / 1e9, UTC).strftime("%H:%M:%S"),
                    "pod": source,
                    "line": line[:MAX_LINE_CHARS],
                }
                for ts, source, line in lines[: p.limit]
            ],
            "count": min(len(lines), p.limit),
        }

    return [
        Tool(
            "loki_logs",
            f"Search logs in namespace {namespace} with LogQL (newest first). Every service logs "
            'JSON with a "level" field, so | json | level=~"WARN|WARNING|ERROR" finds problems.',
            LogParams,
            logs,
        )
    ]
