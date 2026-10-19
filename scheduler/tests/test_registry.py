from __future__ import annotations

import pytest

from app.registry import BackendRegistry


def test_endpoint_change_requires_a_new_health_check(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    replacement = gateway_config.model_copy(deep=True)
    replacement.backend_pools["code-default"].backends[0].url = "http://new:8000"

    registry.reload(replacement)

    assert not registry.has_capacity("code-32k", "interactive")
    assert registry.snapshot()[0]["last_check"] is None
    registry.mark_health("backend-1", True)
    assert registry.has_capacity("code-32k", "interactive")


@pytest.mark.parametrize("changed_field", ["url", "api_key_env"])
def test_active_endpoint_change_is_rejected_atomically(
    gateway_config, changed_field
) -> None:
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    reservation = registry.reserve("active", "code-32k", "alice", "interactive")
    assert reservation is not None
    revision = registry.config_revision
    replacement = gateway_config.model_copy(deep=True)
    spec = replacement.backend_pools["code-default"].backends[0]
    setattr(spec, changed_field, "http://new:8000" if changed_field == "url" else "NEW_KEY")

    with pytest.raises(ValueError, match="active requests"):
        registry.reload(replacement)

    assert registry.config is gateway_config
    assert registry.config_revision == revision
    assert registry.snapshot()[0]["inflight"] == 1
    registry.release(reservation)
    assert registry.snapshot()[0]["inflight"] == 0


def test_capacity_reload_preserves_active_accounting_and_health(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    reservation = registry.reserve("active", "code-32k", "alice", "long")
    assert reservation is not None
    revision = registry.config_revision
    replacement = gateway_config.model_copy(deep=True)
    replacement.backend_pools["code-default"].backends[0].max_concurrent_requests = 2

    registry.reload(replacement)

    assert registry.config_revision == revision + 1
    assert registry.has_capacity("code-32k", "interactive")
    assert not registry.has_capacity("code-32k", "long")
    registry.release(reservation)
    registry.release(reservation)
    assert registry.snapshot()[0]["inflight"] == 0
    assert registry.snapshot()[0]["long_inflight"] == 0


def test_reenabled_backend_requires_a_health_check(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    disabled = gateway_config.model_copy(deep=True)
    disabled.backend_pools["code-default"].backends[0].enabled = False
    registry.reload(disabled)
    registry.reload(gateway_config.model_copy(deep=True))

    assert not registry.has_healthy_backend()


def test_snapshot_includes_host_identity(gateway_config) -> None:
    gateway_config.backend_pools["code-default"].backends[0].host_id = "10.169.25.15"
    registry = BackendRegistry(gateway_config)

    assert registry.snapshot()[0]["host_id"] == "10.169.25.15"
