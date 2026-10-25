#!/usr/bin/env python3
"""Evaluation: runs chaos scenarios, lets the agent diagnose them, scores the agent and the
fixed-rules baseline against the ground truth in scenarios.py.

Diagnosis only: nothing is approved; each scenario is recovered with its own script.
Every run is saved as JSON as soon as it finishes, so the evaluation can be stopped and resumed.

Usage (from the repo root, cluster up, agent at localhost:18003, Prometheus at localhost:9090):
    python3 scripts/eval/run_eval.py --runs 3                       # all six scenarios
    python3 scripts/eval/run_eval.py --runs 1 --scenarios poison    # one quick run
    python3 scripts/eval/run_eval.py --report                       # only rebuild the tables
"""

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scenarios import SCENARIOS, Scenario, action_ok, cause_ok  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "eval" / "results"
AGENT = "http://localhost:18003"
PROMETHEUS = "http://localhost:9090"
LOADGEN = ROOT / "infra" / "chaos" / "loadgen.yaml"


def log(message: str) -> None:
    print(f"{datetime.now():%H:%M:%S}  {message}", flush=True)


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)


def sh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, check=check, capture_output=True, text=True)


def active_alerts() -> list[str]:
    """Sentinel alerts that are pending or firing right now."""
    data = get_json(f"{PROMETHEUS}/api/v1/alerts")["data"]["alerts"]
    return sorted(
        {
            f"{a['labels']['alertname']}({a['state']})"
            for a in data
            if a["labels"].get("namespace") == "sentinel"
        }
    )


def wait_until_quiet(max_minutes: int = 45) -> None:
    """Waits until no sentinel alert is pending or firing (e.g. a slow-burn SLO alert can take
    30 minutes to clear after a database outage)."""
    deadline = time.time() + max_minutes * 60
    while True:
        alerts = active_alerts()
        if not alerts:
            return
        if time.time() > deadline:
            raise SystemExit(f"still not quiet after {max_minutes} min: {alerts}")
        log(f"waiting for alerts to clear: {', '.join(alerts)}")
        time.sleep(60)


def loadgen(on: bool) -> None:
    if on:
        sh("kubectl", "apply", "-f", str(LOADGEN))
    else:
        sh("kubectl", "delete", "-f", str(LOADGEN), "--ignore-not-found")


def find_incident(scenario: Scenario, after_id: int) -> dict | None:
    for summary in get_json(f"{AGENT}/incidents"):
        if summary["id"] > after_id and summary["title"] in scenario.alerts:
            return summary
    return None


def finished_investigation(incident_id: int) -> tuple[dict, dict] | None:
    """The incident's detail and its first finished investigation (on the group's first
    incident, if this one was grouped into another)."""
    detail = get_json(f"{AGENT}/incidents/{incident_id}")
    investigated = detail
    if detail.get("grouped_into"):
        investigated = get_json(f"{AGENT}/incidents/{detail['grouped_into']}")
    for inv in investigated["investigations"]:
        if inv["status"] in ("done", "failed"):
            return investigated, inv
    return None


def score(scenario: Scenario, inv: dict | None) -> dict:
    report = (inv or {}).get("report") or {}
    action = report.get("suggested_action") or {}
    verdict = (inv or {}).get("rule_verdict") or {}
    agent_text = f"{report.get('likely_cause', '')} {report.get('summary', '')}"
    return {
        "agent_cause_ok": bool(report) and cause_ok(scenario, agent_text),
        "agent_action_ok": bool(report)
        and action_ok(scenario, action.get("kind", ""), action.get("target", "")),
        "agent_action": f"{action.get('kind', '-')} {action.get('target', '')}".strip(),
        "baseline_cause_ok": bool(verdict) and cause_ok(scenario, verdict.get("cause", "")),
        "baseline_action_ok": bool(verdict)
        and action_ok(scenario, verdict.get("kind", ""), verdict.get("target", "")),
        "baseline_action": f"{verdict.get('kind', '-')} {verdict.get('target', '')}".strip()
        if verdict
        else "(no rule)",
    }


def run_once(scenario: Scenario, number: int) -> dict:
    out = RESULTS / f"{scenario.key}-{number}.json"
    if out.exists():
        log(f"{out.name} already done, skipping")
        return json.loads(out.read_text())

    wait_until_quiet()
    loadgen(scenario.loadgen)
    if scenario.loadgen:
        time.sleep(60)  # a minute of normal traffic first, so rates have a baseline
    last_id = max((i["id"] for i in get_json(f"{AGENT}/incidents")), default=0)

    log(f"{scenario.key} #{number}: inject")
    injected = datetime.now(UTC)
    sh(f"scripts/chaos/{scenario.script}", "inject", *scenario.inject_args)

    incident, inv, detail = None, None, None
    deadline = time.time() + scenario.timeout_min * 60
    while time.time() < deadline:
        incident = incident or find_incident(scenario, last_id)
        if incident:
            found = finished_investigation(incident["id"])
            if found:
                detail, inv = found
                break
        time.sleep(15)
    finished = datetime.now(UTC)

    log(f"{scenario.key} #{number}: recover")
    sh(f"scripts/chaos/{scenario.script}", "recover", check=False)
    if scenario.loadgen:
        loadgen(False)

    result = {
        "scenario": scenario.key,
        "run": number,
        "injected_at": injected.isoformat(),
        "finished_at": finished.isoformat(),
        "incident": incident,
        "detail": detail,
        "investigation": inv,
        "timed_out": inv is None,
        "seconds_to_report": round(
            (datetime.fromisoformat(inv["finished_at"]) - injected).total_seconds()
        )
        if inv and inv.get("finished_at")
        else None,
        **score(scenario, inv),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str))
    log(
        f"{scenario.key} #{number}: agent cause {'OK' if result['agent_cause_ok'] else 'WRONG'}, "
        f"action {result['agent_action']} ({'OK' if result['agent_action_ok'] else 'WRONG'}); "
        f"baseline {result['baseline_action']} "
        f"({'OK' if result['baseline_action_ok'] else 'WRONG'})"
    )
    return result


def pct(values: list[bool]) -> str:
    return (
        f"{100 * sum(values) / len(values):.0f}% ({sum(values)}/{len(values)})" if values else "-"
    )


def _cause(row: dict) -> str:
    report = ((row.get("investigation") or {}).get("report")) or {}
    text = (report.get("likely_cause") or "-").replace("|", "/").replace("\n", " ")
    return text if len(text) <= 120 else text[:117] + "..."


def report() -> str:
    rows = []
    for path in sorted(RESULTS.glob("*.json")):
        row = json.loads(path.read_text())
        # Score again from the saved answer, so a change to the ground truth applies to old runs.
        row.update(score(SCENARIOS[row["scenario"]], row["investigation"]))
        rows.append(row)
    lines = [
        "| Scenario | Runs | Agent: cause | Agent: action | Baseline: cause | Baseline: action "
        "| Time to report (median) |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, scenario in SCENARIOS.items():
        mine = [r for r in rows if r["scenario"] == key]
        if not mine:
            continue
        times = sorted(r["seconds_to_report"] for r in mine if r["seconds_to_report"] is not None)
        median = (
            f"{times[len(times) // 2] // 60} min {times[len(times) // 2] % 60:02d} s"
            if times
            else "-"
        )
        lines.append(
            f"| {scenario.title} | {len(mine)} | {pct([r['agent_cause_ok'] for r in mine])} "
            f"| {pct([r['agent_action_ok'] for r in mine])} "
            f"| {pct([r['baseline_cause_ok'] for r in mine])} "
            f"| {pct([r['baseline_action_ok'] for r in mine])} | {median} |"
        )
    if rows:
        lines.append(
            f"| **All** | {len(rows)} | {pct([r['agent_cause_ok'] for r in rows])} "
            f"| {pct([r['agent_action_ok'] for r in rows])} "
            f"| {pct([r['baseline_cause_ok'] for r in rows])} "
            f"| {pct([r['baseline_action_ok'] for r in rows])} | |"
        )
    lines += [
        "",
        "| Run | Agent's cause | Agent suggested | Baseline suggested | Timed out |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['scenario']}-{r['run']} | {_cause(r)} {'✓' if r['agent_cause_ok'] else '✗'} "
        f"| {r['agent_action']} {'✓' if r['agent_action_ok'] else '✗'} "
        f"| {r['baseline_action']} {'✓' if r['baseline_action_ok'] else '✗'} "
        f"| {'yes' if r['timed_out'] else ''} |"
        for r in rows
    ]
    text = "\n".join(lines) + "\n"
    (RESULTS / "summary.md").write_text(text)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--report", action="store_true", help="only rebuild summary.md")
    args = parser.parse_args()
    if not args.report:
        keys = [k.strip() for k in args.scenarios.split(",") if k.strip()]
        unknown = [k for k in keys if k not in SCENARIOS]
        if unknown:
            raise SystemExit(f"unknown scenarios: {unknown}; known: {list(SCENARIOS)}")
        try:
            for key in keys:
                for number in range(1, args.runs + 1):
                    run_once(SCENARIOS[key], number)
        finally:
            loadgen(False)
    print(report())


if __name__ == "__main__":
    main()
