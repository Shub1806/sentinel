import pytest

from app.config import ConfigError, load_settings


def test_defaults():
    s = load_settings({"KAFKA_BOOTSTRAP_SERVERS": "localhost:19092"})
    assert s.kafka_bootstrap_servers == "localhost:19092"
    assert s.input_topic == "order.created"
    assert s.output_topic == "payment.done"
    assert s.max_amount_cents == 100_000
    assert s.processing_delay_ms == 0


def test_missing_bootstrap_servers_fails():
    with pytest.raises(ConfigError, match="KAFKA_BOOTSTRAP_SERVERS"):
        load_settings({})


@pytest.mark.parametrize("bad", ["abc", "1.5", "-1"])
def test_bad_number_fails(bad):
    with pytest.raises(ConfigError, match="PROCESSING_DELAY_MS"):
        load_settings({"KAFKA_BOOTSTRAP_SERVERS": "x:1", "PROCESSING_DELAY_MS": bad})
