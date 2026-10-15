"""Settings for the payments service, read from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigError(Exception):
    """Raised when a required setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    kafka_bootstrap_servers: str
    input_topic: str = "order.created"
    output_topic: str = "payment.done"
    consumer_group: str = "payments"
    max_amount_cents: int = 100_000  # orders above $1,000.00 are declined
    processing_delay_ms: int = 0  # artificial delay, used later for chaos testing


def _int_setting(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc
    if value < 0:
        raise ConfigError(f"{name} must not be negative, got {value}")
    return value


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    servers = env.get("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not servers:
        raise ConfigError("KAFKA_BOOTSTRAP_SERVERS is required (for example localhost:19092)")
    return Settings(
        kafka_bootstrap_servers=servers,
        input_topic=env.get("INPUT_TOPIC", "order.created"),
        output_topic=env.get("OUTPUT_TOPIC", "payment.done"),
        consumer_group=env.get("CONSUMER_GROUP", "payments"),
        max_amount_cents=_int_setting(env, "MAX_AMOUNT_CENTS", 100_000),
        processing_delay_ms=_int_setting(env, "PROCESSING_DELAY_MS", 0),
    )
