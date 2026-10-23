"""The worker keeps reconnecting while Redpanda is down, instead of stopping."""

import asyncio
import logging

import pytest
from aiokafka.errors import KafkaConnectionError

from app.config import Settings
from app.worker import PaymentsWorker


class StopTest(Exception):
    """Raised by the fake sleep to end a test after enough attempts."""


def make_worker(delays: list[float], stop_after: int, servers: str = "unused:1") -> PaymentsWorker:
    return PaymentsWorker(
        Settings(kafka_bootstrap_servers=servers), sleep=recording_sleep(delays, stop_after)
    )


def recording_sleep(delays: list[float], stop_after: int):
    async def sleep(seconds: float) -> None:
        delays.append(seconds)
        if len(delays) >= stop_after:
            raise StopTest()

    return sleep


def test_unreachable_broker_is_retried_with_growing_waits(caplog):
    # Port 1 refuses connections, so the real Kafka clients fail to start.
    delays: list[float] = []
    worker = make_worker(delays, stop_after=7, servers="127.0.0.1:1")

    with caplog.at_level(logging.WARNING, logger="payments.worker"), pytest.raises(StopTest):
        asyncio.run(worker.run())

    assert delays == [1, 2, 4, 8, 16, 30, 30]
    assert worker.ready is False
    message = "lost connection to Redpanda, reconnecting"
    warnings = [r for r in caplog.records if r.getMessage() == message]
    assert len(warnings) == 7
    assert warnings[0].failures == 1 and warnings[0].retry_in_ms == 1000
    assert "KafkaConnectionError" in warnings[0].error


def test_wait_starts_over_after_a_successful_connect():
    delays: list[float] = []
    worker = make_worker(delays, stop_after=5)
    attempts = 0

    async def fake_consume():
        nonlocal attempts
        attempts += 1
        if attempts == 4:
            worker._connected()  # connected, then lost again
        raise KafkaConnectionError("broker gone")

    worker._consume = fake_consume
    with pytest.raises(StopTest):
        asyncio.run(worker.run())

    assert delays == [1, 2, 4, 1, 2]


def test_ready_only_while_connected():
    seen: list[bool] = []
    worker = make_worker([], stop_after=1)

    async def fake_consume():
        worker._connected()
        seen.append(worker.ready)
        worker.ready = False  # what _consume's finally block does
        raise KafkaConnectionError("broker gone")

    worker._consume = fake_consume
    with pytest.raises(StopTest):
        asyncio.run(worker.run())

    assert seen == [True]
    assert worker.ready is False


def test_other_errors_still_stop_the_worker():
    # A bug is not a connection problem: let it fail loudly (liveness then restarts the pod).
    worker = make_worker([], stop_after=1)

    async def fake_consume():
        raise ValueError("bug")

    worker._consume = fake_consume
    with pytest.raises(ValueError):
        asyncio.run(worker.run())


def test_stops_cleanly_when_consuming_ends():
    worker = make_worker([], stop_after=1)

    async def fake_consume():
        return None

    worker._consume = fake_consume
    asyncio.run(worker.run())  # returns, no retry
