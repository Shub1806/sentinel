#!/usr/bin/env python3
"""Replay: sends the evidence recorded in each evaluation run to a model again, without a cluster.

Every live run in eval/results/ saved the incident, the initial findings and any tool results.
This rebuilds the exact first message the agent sent (same alerts, same findings, same time),
runs the agent's own investigation loop against a model, and scores the answer with the same
ground truth as the live runs. Tool calls are answered from the recording; a call that was not
recorded gets an error and is counted, so the comparison stays honest.

Uses for this:
  - compare models on identical evidence (the live runs can't: every run sees a new cluster state)
  - check how repeatable one model is on the same input

Needs the agent's Python environment (it imports the agent code) and a model server with an
OpenAI-compatible API. From the repo root:
    services/agent/.venv/bin/python scripts/eval/replay.py --models qwen3:4b,llama3.2:3b
    services/agent/.venv/bin/python scripts/eval/replay.py --models qwen3:4b --scenarios poison
    services/agent/.venv/bin/python scripts/eval/replay.py --report
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "services" / "agent"))

import httpx2  # noqa: E402
from app.incidents import AlertState, Incident  # noqa: E402
from app.investigator import Investigator  # noqa: E402
from app.llm import ChatModel  # noqa: E402
from app.records import Investigation  # noqa: E402
from app.tools.base import Tool, ToolError  # noqa: E402
from app.tools.kubernetes import kubernetes_tools  # noqa: E402
from app.tools.loki import loki_tools  # noqa: E402
from app.tools.prometheus import prometheus_tools  # noqa: E402
from app.triage import Finding  # noqa: E402
from run_eval import RESULTS, pct, score  # noqa: E402
from scenarios import SCENARIOS  # noqa: E402

REPLAY = ROOT / "eval" / "replay"
# The same limits the deployed agent uses (infra/helm-values/agent.yaml and app/config.py).
TOOL_MAX_CHARS = 3000
MAX_STEPS = 8
TIMEOUT_SECONDS = 600.0


def _time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def incident_from(detail: dict) -> Incident:
    """The investigated incident, as the agent's own Incident object."""
    alerts = {
        a["fingerprint"]: AlertState(
            fingerprint=a["fingerprint"],
            name=a["name"],
            status=a["status"],
            labels=a["labels"],
            annotations=a.get("annotations") or {},
            starts_at=_time(a["starts_at"]),
            ends_at=_time(a.get("ends_at")),
        )
        for a in detail["alerts"]
    }
    opened = _time(detail["opened_at"])
    return Incident(
        id=detail["id"],
        group_key="",
        title=detail["title"],
        status=detail["status"],
        opened_at=opened,
        updated_at=opened,
        alerts=alerts,
    )


class RecordedTriage:
    """Hands back the initial findings of the live run instead of collecting new ones."""

    def __init__(self, steps: list[dict]):
        self.findings = [
            Finding(s["tool"], s["arguments"], s["result"], s["seconds"])
            for s in steps
            if s["auto"]
        ]

    async def collect(self, incident: Incident) -> list[Finding]:
        return list(self.findings)


def _key(tool: Tool, arguments: dict) -> str | None:
    try:
        params = tool.params.model_validate(arguments)
    except ValueError:
        return None
    return json.dumps(params.model_dump(), sort_keys=True, default=str)


def recorded_tools(steps: list[dict], unrecorded: list[dict]) -> dict[str, Tool]:
    """The agent's real tool definitions (same names, descriptions and parameters), answering
    from the recording. Calls that weren't recorded are added to `unrecorded`."""
    unused = httpx2.AsyncClient()
    live = [
        *prometheus_tools(unused, "http://unused"),
        *loki_tools(unused, "http://unused", "sentinel"),
        *kubernetes_tools(None, "sentinel"),
    ]
    tools = {}
    for tool in live:
        answers = {_key(tool, s["arguments"]): s["result"] for s in steps if s["tool"] == tool.name}

        async def run(params, tool=tool, answers=answers):
            key = json.dumps(params.model_dump(), sort_keys=True, default=str)
            if key not in answers:
                unrecorded.append({"tool": tool.name, "arguments": params.model_dump()})
                raise ToolError("no recorded result for this call (replay of a past incident)")
            text = answers[key]
            try:
                return json.loads(text)  # serialised again by the agent, exactly as live
            except ValueError:
                return text

        tools[tool.name] = Tool(tool.name, tool.description, tool.params, run)
    return tools


async def replay(result: dict, model) -> dict:
    """Runs one recorded evaluation run through `model` (anything with .model and .chat())."""
    live = result["investigation"]
    incident = incident_from(result["detail"])
    started = _time(live["started_at"])
    unrecorded: list[dict] = []
    investigator = Investigator(
        recorded_tools(live["steps"], unrecorded),
        TOOL_MAX_CHARS,
        MAX_STEPS,
        TIMEOUT_SECONDS,
        clock=lambda: started,  # the prompt states the time of the live run
    )
    investigator.triage = RecordedTriage(live["steps"])
    inv = Investigation(id=0, incident_id=incident.id, model=model.model)
    began = time.monotonic()
    await investigator.run(incident, model, inv)
    seconds = round(time.monotonic() - began, 1)

    report = inv.report.model_dump() if inv.report else None
    answer = {
        "status": inv.status,
        "error": inv.error,
        "report": report,
        "rule_verdict": inv.rule_verdict,
        "raw_answer": "" if report else inv.raw_answer,
    }
    scenario = SCENARIOS[result["scenario"]]
    return {
        "scenario": result["scenario"],
        "run": result["run"],
        "model": model.model,
        "live_model": live["model"],
        "seconds": seconds,
        "tokens": inv.tokens,
        "model_tool_calls": [
            {"tool": s.tool, "arguments": s.arguments} for s in inv.steps if not s.auto
        ],
        "unrecorded_calls": unrecorded,
        "investigation": answer,
        **{k: v for k, v in score(scenario, answer).items() if k.startswith("agent_")},
    }


def _model_dir(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


async def replay_all(models: list[str], keys: list[str], base_url: str, results: Path, out: Path):
    runs = [
        json.loads(p.read_text())
        for p in sorted(results.glob("*.json"))
        if p.stem.rsplit("-", 1)[0] in keys
    ]
    async with httpx2.AsyncClient(timeout=180) as client:
        for name in models:
            model = ChatModel(client, base_url, name)
            folder = out / _model_dir(name)
            folder.mkdir(parents=True, exist_ok=True)
            for run in runs:
                path = folder / f"{run['scenario']}-{run['run']}.json"
                if path.exists():
                    print(f"{name}  {path.name} already done, skipping", flush=True)
                    continue
                row = await replay(run, model)
                path.write_text(json.dumps(row, indent=2, default=str))
                extra = (
                    f", {len(row['unrecorded_calls'])} unrecorded tool calls"
                    if row["unrecorded_calls"]
                    else ""
                )
                cause = "OK" if row["agent_cause_ok"] else "WRONG"
                action = "OK" if row["agent_action_ok"] else "WRONG"
                print(
                    f"{name}  {path.stem}: cause {cause}, "
                    f"action {row['agent_action']} ({action}), {row['seconds']} s{extra}",
                    flush=True,
                )


def _cause(row: dict) -> str:
    report = (row.get("investigation") or {}).get("report") or {}
    text = (report.get("likely_cause") or "-").replace("|", "/").replace("\n", " ")
    return text if len(text) <= 100 else text[:97] + "..."


def report(results: Path = RESULTS, out: Path = REPLAY) -> str:
    """One table per comparison: the live runs, then each replayed model, on the same runs."""
    live = {}
    for p in sorted(results.glob("*.json")):
        row = json.loads(p.read_text())
        row.update(score(SCENARIOS[row["scenario"]], row["investigation"]))
        live[(row["scenario"], row["run"])] = row
    replays: dict[str, dict] = {}
    for folder in sorted(d for d in out.glob("*") if d.is_dir()):
        for p in sorted(folder.glob("*.json")):
            row = json.loads(p.read_text())
            # Score again, so a change to the ground truth applies to old replays too.
            row.update(score(SCENARIOS[row["scenario"]], row["investigation"]))
            replays.setdefault(row["model"], {})[(row["scenario"], row["run"])] = row

    columns = [
        ("live " + (next(iter(live.values()))["investigation"]["model"] if live else ""), live)
    ]
    columns += [(f"replay {m}", rows) for m, rows in replays.items()]
    header = "| Scenario | " + " | ".join(f"{name}: cause / action" for name, _ in columns) + " |"
    lines = [header, "|---|" + "---|" * len(columns)]
    for key, scenario in SCENARIOS.items():
        cells = []
        for _, rows in columns:
            mine = [r for (s, _), r in rows.items() if s == key]
            cells.append(
                f"{pct([r['agent_cause_ok'] for r in mine])} / "
                f"{pct([r['agent_action_ok'] for r in mine])}"
                if mine
                else "-"
            )
        lines.append(f"| {scenario.title} | " + " | ".join(cells) + " |")
    totals = [
        f"{pct([r['agent_cause_ok'] for r in rows.values()])} / "
        f"{pct([r['agent_action_ok'] for r in rows.values()])}"
        for _, rows in columns
    ]
    lines.append("| **All** | " + " | ".join(totals) + " |")

    for model, rows in replays.items():
        calls = sum(len(r["model_tool_calls"]) for r in rows.values())
        missing = sum(len(r["unrecorded_calls"]) for r in rows.values())
        times = sorted(r["seconds"] for r in rows.values())
        lines += [
            "",
            f"### replay {model}: {len(rows)} runs, median {times[len(times) // 2]} s, "
            f"{calls} tool calls by the model ({missing} not in the recording)",
            "",
            "| Run | Live cause | Replayed cause | Replayed action |",
            "|---|---|---|---|",
        ]
        for (scenario, run), row in sorted(rows.items()):
            before = live.get((scenario, run), {})
            lines.append(
                f"| {scenario}-{run} | {_cause(before)} "
                f"{'✓' if before.get('agent_cause_ok') else '✗'} "
                f"| {_cause(row)} {'✓' if row['agent_cause_ok'] else '✗'} "
                f"| {row['agent_action']} {'✓' if row['agent_action_ok'] else '✗'} |"
            )
    text = "\n".join(lines) + "\n"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(text)
    return text


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--models", default="qwen3:4b", help="comma-separated model names")
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--report", action="store_true", help="only rebuild summary.md")
    args = parser.parse_args()
    if not args.report:
        keys = [k.strip() for k in args.scenarios.split(",") if k.strip()]
        unknown = [k for k in keys if k not in SCENARIOS]
        if unknown:
            raise SystemExit(f"unknown scenarios: {unknown}; known: {list(SCENARIOS)}")
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        asyncio.run(replay_all(models, keys, args.base_url.rstrip("/"), RESULTS, REPLAY))
    print(report())


if __name__ == "__main__":
    main()
