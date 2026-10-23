"""Builds the full set of tools from the settings."""

import os

import httpx2

from app.config import Settings
from app.tools.base import Tool
from app.tools.kubernetes import KubeApi, kubernetes_tools
from app.tools.loki import loki_tools
from app.tools.prometheus import prometheus_tools


def build_tools(settings: Settings, client: httpx2.AsyncClient, env=os.environ) -> dict[str, Tool]:
    kube = KubeApi.in_cluster(settings.tool_timeout_seconds, env)
    tools = [
        *prometheus_tools(client, settings.prometheus_url),
        *loki_tools(client, settings.loki_url, settings.namespace),
        *kubernetes_tools(kube, settings.namespace),
    ]
    return {t.name: t for t in tools}
