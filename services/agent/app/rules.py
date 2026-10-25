"""Fixed rules: a diagnosis without a model, from the same initial findings the model gets.

Shown next to the model's suggestion on the page (a strong rule that disagrees gets a warning),
and used as the simple baseline the agent is compared against in the evaluation.
"""

import json
from collections.abc import Iterable

from app.incidents import Incident

# Known alerts and what they usually mean here. Hints only: they don't look at the evidence.
ALERT_HINTS: dict[str, tuple[str, str]] = {
    "InvalidMessagesReceived": ("none", "a producer sent events that fail validation"),
    "OutboxBacklog": ("manual", "orders can't publish its events: check the broker (redpanda)"),
    "NotificationErrors": ("manual", "notifications can't save receipts: check S3 (localstack)"),
    "ConsumerLagHigh": ("manual", "a consumer is slower than the events arriving"),
    "PaymentFailureRatioHigh": ("manual", "many payments are declined: check recent orders"),
    "OrdersAvailabilityBudgetBurnFast": (
        "manual",
        "POST /orders is failing: check orders and Postgres",
    ),
    "OrdersAvailabilityBudgetBurnSlow": (
        "manual",
        "POST /orders is failing: check orders and Postgres",
    ),
    "OrdersLatencyBudgetBurnFast": ("manual", "POST /orders is slow: check orders and Postgres"),
    "OrdersLatencyBudgetBurnSlow": ("manual", "POST /orders is slow: check orders and Postgres"),
}


def _results(steps: Iterable, tool: str) -> list[dict]:
    out = []
    for step in steps:
        if step.tool != tool or step.result.startswith("error"):
            continue
        try:
            out.append(json.loads(step.result))
        except ValueError:
            continue
    return out


def evaluate(incident: Incident, steps: Iterable) -> dict | None:
    """The fixed-rule verdict, or None if no rule applies.

    Returns {"rule", "kind", "target", "cause", "strong"}; strong = based on evidence.
    """
    steps = list(steps)
    for diff in _results(steps, "k8s_rollout_diff"):
        new, old = diff.get("newest") or {}, diff.get("previous") or {}
        changes = [c for c in diff.get("changes", []) if c != "no differences in the pod template"]
        if changes and diff.get("new_pods_healthy") is False and old.get("ready", 0) > 0:
            return {
                "rule": "bad rollout",
                "kind": "rollout_undo",
                "target": diff.get("deployment", ""),
                "cause": (
                    f"the latest rollout of {diff.get('deployment')} changed: "
                    f"{'; '.join(changes)}; its new pods are not ready "
                    f"({new.get('ready', 0)}/{new.get('replicas', 0)}) while revision "
                    f"{old.get('revision')} was ({old.get('ready')}/{old.get('replicas')})"
                ),
                "strong": True,
            }
    for alert in incident.alerts.values():
        if alert.name in ALERT_HINTS:
            kind, cause = ALERT_HINTS[alert.name]
            return {
                "rule": f"alert {alert.name}",
                "kind": kind,
                "target": "",
                "cause": cause,
                "strong": False,
            }
    return None


def disagrees(verdict: dict | None, kind: str, target: str) -> bool:
    """True if a strong rule suggests something else than the model did."""
    if not verdict or not verdict.get("strong"):
        return False
    return (verdict["kind"], verdict["target"]) != (kind, target)
