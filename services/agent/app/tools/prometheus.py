"""Prometheus tools: an instant query, and a range query summarised per series."""

from datetime import UTC, datetime, timedelta

import httpx2
from pydantic import BaseModel, Field

from app.tools.base import Tool, ToolError

MAX_SERIES = 30


class QueryParams(BaseModel):
    query: str = Field(description='PromQL expression, for example: up{namespace="sentinel"}')


class RangeParams(BaseModel):
    query: str = Field(description="PromQL expression to evaluate over time")
    minutes: int = Field(default=30, ge=1, le=360, description="How far back to look")
    step_seconds: int = Field(default=60, ge=15, le=3600, description="Resolution")


async def _get(client: httpx2.AsyncClient, url: str, params: dict) -> list:
    response = await client.get(url, params=params)
    body = (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if response.status_code != 200 or body.get("status") != "success":
        raise ToolError(
            f"Prometheus said {response.status_code}: {body.get('error', response.text[:300])}"
        )
    return body["data"]["result"]


def _number(value: str) -> float | str:
    try:
        return round(float(value), 6)
    except ValueError:
        return value


def prometheus_tools(client: httpx2.AsyncClient, base_url: str, clock=lambda: datetime.now(UTC)):
    async def query(p: QueryParams) -> dict:
        result = await _get(client, f"{base_url}/api/v1/query", {"query": p.query})
        series = [{"labels": r["metric"], "value": _number(r["value"][1])} for r in result]
        return {"series": series[:MAX_SERIES], "total_series": len(series)}

    async def query_range(p: RangeParams) -> dict:
        end = clock()
        start = end - timedelta(minutes=p.minutes)
        result = await _get(
            client,
            f"{base_url}/api/v1/query_range",
            {
                "query": p.query,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": p.step_seconds,
            },
        )
        series = []
        for r in result[:MAX_SERIES]:
            points = [
                (datetime.fromtimestamp(t, UTC).strftime("%H:%M"), _number(v))
                for t, v in r["values"]
            ]
            numbers = [v for _, v in points if isinstance(v, float)]
            series.append(
                {
                    "labels": r["metric"],
                    "first": points[0] if points else None,
                    "last": points[-1] if points else None,
                    "min": min(numbers) if numbers else None,
                    "max": max(numbers) if numbers else None,
                    "recent": points[-10:],
                }
            )
        return {
            "from": start.strftime("%H:%M"),
            "to": end.strftime("%H:%M"),
            "series": series,
            "total_series": len(result),
        }

    return [
        Tool(
            "prometheus_query",
            "Run a PromQL instant query (the value right now). Use for current error rates, lag, "
            "restarts, outbox age and similar.",
            QueryParams,
            query,
        ),
        Tool(
            "prometheus_range",
            "Run a PromQL query over the last N minutes. Returns first/last/min/max and the last "
            "10 points per series. Use to see when something started or how it changed.",
            RangeParams,
            query_range,
        ),
    ]
