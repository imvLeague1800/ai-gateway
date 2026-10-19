from dataclasses import replace

import pytest

from app.settings import Settings


@pytest.mark.parametrize("field", [
    "queue_timeout_seconds", "request_timeout_seconds",
    "health_interval_seconds", "health_timeout_seconds",
    "tokenize_timeout_seconds", "upstream_connect_timeout_seconds",
    "upstream_read_timeout_seconds", "upstream_write_timeout_seconds",
    "upstream_pool_timeout_seconds",
])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_rejects_invalid_timeouts(field, value) -> None:
    with pytest.raises(ValueError, match=field):
        replace(Settings(), **{field: value})


@pytest.mark.parametrize("field", [
    "max_queue_depth", "max_queued_per_principal", "max_active_per_principal",
    "max_request_body_bytes",
])
@pytest.mark.parametrize("value", [0, -1, 0.5, True])
def test_rejects_invalid_limits(field, value) -> None:
    with pytest.raises(ValueError, match=field):
        replace(Settings(), **{field: value})


def test_reads_configurable_timeouts_from_env(monkeypatch) -> None:
    monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "1300")
    monkeypatch.setenv("UPSTREAM_CONNECT_TIMEOUT_SECONDS", "11")
    monkeypatch.setenv("UPSTREAM_READ_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("UPSTREAM_WRITE_TIMEOUT_SECONDS", "13")
    monkeypatch.setenv("UPSTREAM_POOL_TIMEOUT_SECONDS", "14")
    settings = Settings.from_env()
    assert settings.request_timeout_seconds == 1300
    assert settings.upstream_connect_timeout_seconds == 11
    assert settings.upstream_read_timeout_seconds == 12
    assert settings.upstream_write_timeout_seconds == 13
    assert settings.upstream_pool_timeout_seconds == 14


def test_rejects_misspelled_boolean_env(monkeypatch) -> None:
    monkeypatch.setenv("START_HEALTH_MONITOR", "tru")
    with pytest.raises(ValueError, match="START_HEALTH_MONITOR"):
        Settings.from_env()


def test_zero_queue_promotion_is_valid() -> None:
    assert replace(Settings(), long_queue_promote_seconds=0).long_queue_promote_seconds == 0


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True])
def test_rejects_invalid_queue_promotion(value) -> None:
    with pytest.raises(ValueError, match="long_queue_promote_seconds"):
        replace(Settings(), long_queue_promote_seconds=value)


@pytest.mark.parametrize("queue_timeout", [1260, 1500])
def test_queue_timeout_must_leave_time_for_inference(queue_timeout) -> None:
    with pytest.raises(ValueError, match="queue_timeout_seconds must be less"):
        replace(Settings(), queue_timeout_seconds=queue_timeout)
