"""The six chaos scenarios as evaluation cases: how to cause them, which alert opens the
incident, and what a correct diagnosis says (the ground truth used for scoring)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    script: str  # in scripts/chaos/
    inject_args: tuple[str, ...]
    loadgen: bool  # needs order traffic for its alerts to fire
    alerts: tuple[str, ...]  # the incident to score is the first one opened for one of these
    cause_words: tuple[str, ...]  # a correct cause mentions at least one (case-insensitive)
    actions: tuple[tuple[str, str], ...]  # correct suggestions: (kind, target); "" = any target
    timeout_min: int  # from inject until a finished investigation is expected


SCENARIOS = {
    s.key: s
    for s in [
        Scenario(
            "bad-deploy",
            "Bad deploy: payments crash-loops",
            "payments-bad-config.sh",
            (),
            False,
            ("PodRestarted", "KubePodCrashLooping", "KubePodNotReady"),
            ("MAX_AMOUNT_CENTS",),
            (("rollout_undo", "payments"),),
            8,
        ),
        Scenario(
            "db-outage",
            "Database outage",
            "database-outage.sh",
            (),
            True,
            (
                "OrdersAvailabilityBudgetBurnFast",
                "OrdersAvailabilityBudgetBurnSlow",
                "OrdersLatencyBudgetBurnFast",
            ),
            ("database", "postgres", "orders-db", "hibernat"),
            (("manual", ""),),
            18,
        ),
        Scenario(
            "s3-outage",
            "S3 outage",
            "s3-outage.sh",
            (),
            True,
            ("NotificationErrors",),
            ("s3", "localstack"),
            (("manual", ""),),
            12,
        ),
        Scenario(
            "slow-consumer",
            "Slow consumer: payments falls behind",
            "slow-consumer.sh",
            ("2000",),
            True,
            ("ConsumerLagHigh",),
            ("PROCESSING_DELAY_MS", "delay", "slow"),
            (("rollout_undo", "payments"), ("manual", "")),
            25,
        ),
        Scenario(
            "poison",
            "Poison messages",
            "poison-messages.sh",
            (),
            False,
            ("InvalidMessagesReceived",),
            ("invalid", "producer", "validation", "malformed"),
            (("none", ""), ("manual", "")),
            5,
        ),
        Scenario(
            "broker-outage",
            "Broker outage",
            "broker-outage.sh",
            (),
            True,
            ("OutboxBacklog",),
            ("redpanda", "broker", "kafka"),
            (("manual", ""),),
            10,
        ),
    ]
}


def cause_ok(scenario: Scenario, text: str) -> bool:
    text = text.lower()
    return any(word.lower() in text for word in scenario.cause_words)


def action_ok(scenario: Scenario, kind: str, target: str) -> bool:
    return any(k == kind and (t == "" or t == target) for k, t in scenario.actions)
