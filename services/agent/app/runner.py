"""Starts investigations and post-mortems in the background, one at a time (a local model
has one GPU)."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from app import postmortem
from app.incidents import Incident, IncidentStore
from app.investigator import Investigation, Investigator
from app.llm import ChatModel
from app.metrics import INVESTIGATION_SECONDS, INVESTIGATIONS, TOOL_CALLS


class InvestigationRunner:
    def __init__(
        self,
        investigator: Investigator,
        model_factory: Callable[[str], ChatModel],
        store: IncidentStore | None = None,
    ):
        self.investigator = investigator
        self.model_factory = model_factory
        # Where investigations are numbered and saved. Set by the app if not given here.
        self.store = store or IncidentStore()
        investigator.on_change = lambda inv: self.store.save_investigation(inv)
        self._one_at_a_time = asyncio.Semaphore(1)
        self._tasks: set[asyncio.Task] = set()

    def start(self, incident: Incident, model: str) -> Investigation:
        inv = self.store.new_investigation(incident, model)
        task = asyncio.create_task(self._run(incident, inv))
        self._tasks.add(task)  # keep a reference until it finishes
        task.add_done_callback(self._tasks.discard)
        return inv

    async def _run(self, incident: Incident, inv: Investigation) -> None:
        async with self._one_at_a_time:
            await self.investigator.run(incident, self.model_factory(inv.model), inv)
        INVESTIGATIONS.labels(status=inv.status).inc()
        if inv.started_at and inv.finished_at:
            INVESTIGATION_SECONDS.observe((inv.finished_at - inv.started_at).total_seconds())
        for step in inv.steps:
            TOOL_CALLS.labels(tool=step.tool).inc()

    def start_postmortem(self, incident: Incident, model: str) -> None:
        """Writes the post-mortem; with an empty model name, facts only."""
        self.store.new_postmortem(incident)
        task = asyncio.create_task(self._write_postmortem(incident, model))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _write_postmortem(self, incident: Incident, model: str) -> None:
        async with self._one_at_a_time:
            chat = self.model_factory(model) if model else None
            await postmortem.write(
                incident,
                incident.postmortem,
                chat,
                lambda: datetime.now(UTC),
                related=self.store.related(incident),
            )
        self.store.save_postmortem(incident.postmortem)

    async def wait(self) -> None:
        """Waits for every running investigation and post-mortem (used in tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def cancel(self) -> None:
        for task in self._tasks:
            task.cancel()
