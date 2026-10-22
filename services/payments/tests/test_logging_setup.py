import json
import logging

import pytest

from app.logging_setup import configure_logging

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def restore_logging():
    """configure_logging changes global loggers; put them back after each test."""
    names = ("", *UVICORN_LOGGERS)
    saved = {}
    for name in names:
        lg = logging.getLogger(name)
        saved[name] = (lg.handlers[:], lg.level, lg.propagate, lg.disabled)
    yield
    for name, (handlers, level, propagate, disabled) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers, lg.propagate, lg.disabled = handlers, propagate, disabled
        lg.setLevel(level)


def _uvicorn_like_setup():
    """What uvicorn does at startup: its own plain-text handler, no propagation."""
    for name in UVICORN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers = [logging.StreamHandler()]
        lg.propagate = False


def test_uvicorn_messages_come_out_as_json(capsys):
    _uvicorn_like_setup()
    configure_logging()

    logging.getLogger("uvicorn.error").info("Application startup complete.")

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1  # once, not duplicated by uvicorn's own handler
    entry = json.loads(lines[0])
    assert entry["level"] == "INFO"
    assert entry["logger"] == "uvicorn.error"
    assert entry["msg"] == "Application startup complete."


def test_access_log_is_silenced(capsys):
    _uvicorn_like_setup()
    configure_logging()

    logging.getLogger("uvicorn.access").info('10.42.0.1 - "GET /readyz HTTP/1.1" 200')

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_app_logs_still_come_out_as_json(capsys):
    configure_logging()

    logging.getLogger("payments").info("payment processed", extra={"order_id": "o-1"})

    entry = json.loads(capsys.readouterr().out)
    assert entry["msg"] == "payment processed"
    assert entry["order_id"] == "o-1"
