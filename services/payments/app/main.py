"""FastAPI app: runs the worker in the background and serves health checks."""

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Response

from app.config import Settings, load_settings
from app.logging_setup import configure_logging
from app.worker import PaymentsWorker

log = logging.getLogger("payments")


def _log_worker_exit(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("worker stopped with an error", exc_info=task.exception())


def create_app(
    settings_loader: Callable[[], Settings] = load_settings,
    worker_factory: Callable[[Settings], PaymentsWorker] = PaymentsWorker,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        settings = settings_loader()  # a bad setting stops startup here
        worker = worker_factory(settings)
        task = asyncio.create_task(worker.run())
        task.add_done_callback(_log_worker_exit)
        app.state.worker = worker
        app.state.task = task
        yield
        task.cancel()
        # The worker may have already crashed; that error was logged above.
        with suppress(asyncio.CancelledError, Exception):
            await task

    app = FastAPI(title="payments", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz(response: Response) -> dict:
        """Liveness: is the worker still running? If not, Kubernetes restarts the pod."""
        if app.state.task.done():
            response.status_code = 503
            return {"status": "worker stopped"}
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz(response: Response) -> dict:
        """Readiness: is the worker connected to Redpanda?"""
        if not app.state.worker.ready:
            response.status_code = 503
            return {"status": "not ready"}
        return {"status": "ready"}

    return app


app = create_app()
