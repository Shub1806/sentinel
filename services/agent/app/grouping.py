"""Which service an alert is about, so alerts with one cause end up in one incident.

A bad payments rollout fires PodRestarted, KubePodCrashLooping, KubePodNotReady, TargetDown and
KubeHpaMaxedOut: five alert groups, all about "payments". The first opens the incident; the
others, if they arrive while it is still firing and soon after, become related incidents of it.
"""

import re

# Kubernetes builds pod-name hashes from this alphabet (no vowels, no 0/1/3), so a Deployment
# pod "payments-58f84654df-plx78" can be told apart from a Job pod "redpanda-create-topics-q9flv".
_SAFE = "bcdfghjklmnpqrstvwxz2456789"
_DEPLOYMENT_POD = re.compile(rf"^(.+)-[{_SAFE}]{{6,10}}-[{_SAFE}]{{5}}$")
_STATEFUL_POD = re.compile(r"^(.+)-\d+$")  # orders-db-1 -> orders-db, redpanda-0 -> redpanda
_JOB_POD = re.compile(rf"^(.+)-[{_SAFE}]{{5}}$")  # redpanda-configure-pmckn -> redpanda-configure

# Labels that name the thing an alert is about, most specific first.
_SPECIFIC = ("deployment", "statefulset", "horizontalpodautoscaler", "redpanda_group")
# Labels used only when none of the above (or a pod) is present: on Kubernetes alerts,
# job/service name the exporter that produced the metric, not the affected service.
_FALLBACK = ("service", "job")


def deployment_of(pod: str) -> str | None:
    """payments-58f84654df-plx78 -> payments; None if the name isn't a Deployment pod's."""
    match = _DEPLOYMENT_POD.match(pod)
    return match.group(1) if match else None


def workload_of(pod: str) -> str:
    """payments-58f84654df-plx78 -> payments; orders-db-1 -> orders-db; a Job's pod -> the Job."""
    for pattern in (_DEPLOYMENT_POD, _STATEFUL_POD, _JOB_POD):
        match = pattern.match(pod)
        if match:
            return match.group(1)
    return pod


def _is_exporter(name: str) -> bool:
    return name.startswith("monitoring-") or name in ("kube-state-metrics", "node-exporter")


def subjects(labels: dict[str, str]) -> set[str]:
    found = {labels[k] for k in _SPECIFIC if labels.get(k)}
    if labels.get("pod"):
        found.add(workload_of(labels["pod"]))
    if not found:
        found = {labels[k] for k in _FALLBACK if labels.get(k)}
    return {name for name in found if not _is_exporter(name)}
