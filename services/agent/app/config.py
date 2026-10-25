"""Settings for the agent, read from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when a setting is invalid."""


@dataclass(frozen=True)
class Settings:
    # The namespace the agent investigates. Its Kubernetes permissions only cover this one.
    namespace: str = "sentinel"
    prometheus_url: str = (
        "http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090"
    )
    loki_url: str = "http://loki.logging.svc.cluster.local:3100"
    # Read on startup to pick up alerts that are already firing. Empty = skip.
    alertmanager_url: str = (
        "http://monitoring-kube-prometheus-alertmanager.monitoring.svc.cluster.local:9093"
    )
    # Each tool call gets this long before it gives up.
    tool_timeout_seconds: float = 10.0
    # Tool results are cut to this many characters, so one call can't flood the model's context.
    tool_max_chars: int = 6000
    # SQLite file for incidents and investigations. Empty = memory only (lost on restart).
    db_path: str = ""
    # Any chat model behind an OpenAI-compatible API (for example a local Ollama server).
    # Empty model = investigations are off; incidents are still recorded.
    llm_base_url: str = "http://host.k3d.internal:11434/v1"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: float = 180.0
    # Most tool calls in one investigation, and the time limit for all of it.
    max_steps: int = 8
    investigation_timeout_seconds: float = 600.0


def _positive(env: Mapping[str, str], name: str, default: float, kind=float):
    raw = env.get(name, "")
    if raw == "":
        return default
    try:
        value = kind(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than 0, got {value}")
    return value


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    defaults = Settings()
    return Settings(
        namespace=env.get("WATCH_NAMESPACE") or defaults.namespace,
        prometheus_url=(env.get("PROMETHEUS_URL") or defaults.prometheus_url).rstrip("/"),
        loki_url=(env.get("LOKI_URL") or defaults.loki_url).rstrip("/"),
        alertmanager_url=env.get("ALERTMANAGER_URL", defaults.alertmanager_url).rstrip("/"),
        tool_timeout_seconds=_positive(env, "TOOL_TIMEOUT_SECONDS", defaults.tool_timeout_seconds),
        tool_max_chars=_positive(env, "TOOL_MAX_CHARS", defaults.tool_max_chars, int),
        db_path=env.get("DB_PATH", "").strip(),
        llm_base_url=(env.get("LLM_BASE_URL") or defaults.llm_base_url).rstrip("/"),
        llm_model=env.get("LLM_MODEL", "").strip(),
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_timeout_seconds=_positive(env, "LLM_TIMEOUT_SECONDS", defaults.llm_timeout_seconds),
        max_steps=_positive(env, "MAX_STEPS", defaults.max_steps, int),
        investigation_timeout_seconds=_positive(
            env, "INVESTIGATION_TIMEOUT_SECONDS", defaults.investigation_timeout_seconds
        ),
    )
