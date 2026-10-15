import asyncio

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class FakeWorker:
    def __init__(self, settings, ready=True, crash=False):
        self.ready = ready
        self.crash = crash

    async def run(self):
        if self.crash:
            raise RuntimeError("lost connection")
        await asyncio.Event().wait()  # run forever, like the real worker


def make_client(**worker_kwargs) -> TestClient:
    app = create_app(
        settings_loader=lambda: Settings(kafka_bootstrap_servers="unused:1"),
        worker_factory=lambda s: FakeWorker(s, **worker_kwargs),
    )
    return TestClient(app)


def test_healthy_and_ready():
    with make_client() as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200


def test_not_ready_until_connected():
    with make_client(ready=False) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503


def test_unhealthy_when_worker_crashes():
    with make_client(crash=True) as client:
        assert client.get("/healthz").status_code == 503
