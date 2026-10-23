import pytest

from app.config import ConfigError, Settings, load_settings


def test_defaults():
    assert load_settings({}) == Settings()


def test_overrides_and_trailing_slash():
    s = load_settings(
        {"WATCH_NAMESPACE": "other", "PROMETHEUS_URL": "http://p:9090/", "TOOL_MAX_CHARS": "100"}
    )
    assert (s.namespace, s.prometheus_url, s.tool_max_chars) == ("other", "http://p:9090", 100)


@pytest.mark.parametrize("value", ["abc", "0", "-1"])
def test_bad_numbers_are_rejected(value):
    with pytest.raises(ConfigError):
        load_settings({"TOOL_TIMEOUT_SECONDS": value})
