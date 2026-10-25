"""One investigation: the model calls read-only tools until it can write a report."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime

from app.incidents import Incident
from app.llm import ChatModel, ModelError
from app.prompts import JSON_ONLY, OUT_OF_STEPS, SYSTEM_PROMPT
from app.records import Investigation, Step
from app.report import parse_report
from app.tools.base import Tool, call_tool
from app.triage import Triage, findings_text

log = logging.getLogger("agent.investigator")


def describe_incident(incident: Incident, now: datetime) -> str:
    lines = [f"Current time (UTC): {now:%Y-%m-%d %H:%M:%S}", f"Incident: {incident.title}", ""]
    for alert in incident.alerts.values():
        labels = {k: v for k, v in alert.labels.items() if k != "alertname"}
        lines += [
            f"Alert {alert.name} ({alert.status}), firing since {alert.starts_at:%H:%M:%S} UTC",
            f"  labels: {json.dumps(labels)}",
            f"  summary: {alert.annotations.get('summary', '')}",
            f"  description: {alert.annotations.get('description', '')}",
        ]
    return "\n".join(lines)


class Investigator:
    def __init__(
        self,
        tools: dict[str, Tool],
        tool_max_chars: int,
        max_steps: int,
        timeout_seconds: float,
        clock=lambda: datetime.now(UTC),
        namespace: str = "sentinel",
        triage: bool = True,
        on_change=lambda inv: None,
    ):
        self.tools = tools
        # Called whenever the investigation changes, so it can be saved and shown while it runs.
        self.on_change = on_change
        self.triage = Triage(tools, namespace, tool_max_chars) if triage else None
        self.tool_max_chars = tool_max_chars
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds
        self.clock = clock

    async def run(self, incident: Incident, model: ChatModel, inv: Investigation) -> Investigation:
        inv.status = "running"
        inv.started_at = self.clock()
        self.on_change(inv)
        log.info(
            "investigation started",
            extra={"incident_id": incident.id, "investigation_id": inv.id, "model": model.model},
        )
        try:
            await asyncio.wait_for(self._loop(incident, model, inv), self.timeout_seconds)
            inv.status = "done"
        except TimeoutError:
            inv.status, inv.error = "failed", f"timed out after {self.timeout_seconds:.0f} s"
        except ModelError as exc:
            inv.status, inv.error = "failed", str(exc)
        inv.finished_at = self.clock()
        self.on_change(inv)
        log.info(
            "investigation finished",
            extra={
                "incident_id": incident.id,
                "investigation_id": inv.id,
                "status": inv.status,
                "steps": len(inv.steps),
                "error": inv.error,
                "cause": inv.report.likely_cause if inv.report else "",
            },
        )
        return inv

    async def _loop(self, incident: Incident, model: ChatModel, inv: Investigation) -> None:
        schemas = [t.schema() for t in self.tools.values()]
        first = describe_incident(incident, self.clock())
        if self.triage:
            findings = await self.triage.collect(incident)
            for f in findings:
                inv.steps.append(Step(f.tool, f.arguments, f.result, f.seconds, auto=True))
            self.on_change(inv)
            first += "\n\n" + findings_text(findings)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": first},
        ]
        asked_for_json = False
        for _ in range(self.max_steps):
            reply = await model.chat(messages, schemas)
            inv.tokens += reply.tokens
            if reply.tool_calls:
                await self._run_tools(reply, messages, inv)
                continue
            if self._finish(reply.content, inv):
                return
            if asked_for_json:
                return
            asked_for_json = True
            messages += [
                {"role": "assistant", "content": reply.content},
                {"role": "user", "content": JSON_ONLY},
            ]
        # Out of steps: one last answer, without tools.
        messages.append({"role": "user", "content": OUT_OF_STEPS})
        reply = await model.chat(messages)
        inv.tokens += reply.tokens
        self._finish(reply.content, inv)

    async def _run_tools(self, reply, messages: list[dict], inv: Investigation) -> None:
        messages.append(
            {
                "role": "assistant",
                "content": reply.content,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in reply.tool_calls
                ],
            }
        )
        for call in reply.tool_calls:
            started = time.monotonic()
            if call.problem:
                result = f"error: {call.problem}"
            else:
                result = await call_tool(self.tools, call.name, call.arguments, self.tool_max_chars)
            seconds = round(time.monotonic() - started, 2)
            inv.steps.append(Step(call.name, call.arguments, result, seconds))
            self.on_change(inv)
            log.info(
                "investigation step",
                extra={
                    "investigation_id": inv.id,
                    "tool": call.name,
                    "arguments": call.arguments,
                    "result_chars": len(result),
                    "error": result.startswith("error"),
                },
            )
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    @staticmethod
    def _finish(content: str, inv: Investigation) -> bool:
        inv.raw_answer = content
        inv.report = parse_report(content)
        return inv.report is not None
