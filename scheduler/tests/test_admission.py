from __future__ import annotations

import asyncio

import pytest

from app.admission import (
    AdmissionController,
    ConfigurationChangedError,
    QueueFullError,
    QueueTimeoutError,
)
from app.config import GatewayConfig
from app.registry import BackendRegistry


@pytest.mark.asyncio
async def test_waiter_runs_after_capacity_is_released(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    controller = AdmissionController(registry)

    first = await controller.acquire(
        "request-1", "code-32k", "alice", "interactive", 1
    )
    waiter = asyncio.create_task(
        controller.acquire("request-2", "code-32k", "bob", "interactive", 1)
    )
    await asyncio.sleep(0.01)
    assert not waiter.done()

    await controller.release(first)
    second = await waiter
    assert second.backend.id == "backend-1"
    await controller.release(second)
    assert registry.snapshot()[0]["inflight"] == 0


@pytest.mark.asyncio
async def test_queue_times_out_when_no_backend_is_healthy(gateway_config) -> None:
    controller = AdmissionController(BackendRegistry(gateway_config))
    with pytest.raises(QueueTimeoutError):
        await controller.acquire(
            "request-1", "code-32k", "alice", "interactive", 0.01
        )
    assert controller.queue_depths() == {}


@pytest.mark.asyncio
async def test_active_backend_cannot_be_removed(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    controller = AdmissionController(registry)
    reservation = await controller.acquire(
        "request-1", "code-32k", "alice", "interactive", 1
    )

    replacement = GatewayConfig.model_validate(
        {
            "models": {
                "code-32k": {
                    "backend_pool": "replacement",
                    "upstream_model": "served-model",
                    "max_context_tokens": 1024,
                    "max_output_tokens": 128,
                    "default_output_tokens": 32,
                    "long_request_threshold_tokens": 64,
                }
            },
            "backend_pools": {
                "replacement": {
                    "backends": [
                        {
                            "id": "backend-2",
                            "url": "http://backend-2:8000",
                            "max_context_tokens": 1024,
                            "max_long_requests": 1,
                        }
                    ]
                }
            },
        }
    )
    with pytest.raises(ValueError, match="active requests"):
        await controller.reload(replacement)
    await controller.release(reservation)


@pytest.mark.asyncio
async def test_queue_round_robins_between_principals(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    controller = AdmissionController(registry)
    blocker = await controller.acquire(
        "blocker", "code-32k", "carol", "interactive", 1
    )
    alice_first = asyncio.create_task(
        controller.acquire("alice-1", "code-32k", "alice", "interactive", 1)
    )
    alice_second = asyncio.create_task(
        controller.acquire("alice-2", "code-32k", "alice", "interactive", 1)
    )
    bob = asyncio.create_task(
        controller.acquire("bob-1", "code-32k", "bob", "interactive", 1)
    )
    await asyncio.sleep(0.01)

    await controller.release(blocker)
    first = await alice_first
    await controller.release(first)
    second = await bob
    assert not alice_second.done()
    await controller.release(second)
    third = await alice_second
    await controller.release(third)


@pytest.mark.asyncio
async def test_old_long_request_gets_next_available_slot(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    controller = AdmissionController(registry, long_queue_promote_seconds=0)
    blocker = await controller.acquire(
        "blocker", "code-32k", "carol", "interactive", 1
    )
    long_request = asyncio.create_task(
        controller.acquire("long", "code-32k", "alice", "long", 1)
    )
    interactive = asyncio.create_task(
        controller.acquire("normal", "code-32k", "bob", "interactive", 1)
    )
    await asyncio.sleep(0.01)

    await controller.release(blocker)
    long_reservation = await long_request
    assert not interactive.done()
    await controller.release(long_reservation)
    interactive_reservation = await interactive
    await controller.release(interactive_reservation)


@pytest.mark.asyncio
async def test_per_principal_queue_is_bounded(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    controller = AdmissionController(registry, max_queued_per_principal=1)
    blocker = await controller.acquire(
        "blocker", "code-32k", "carol", "interactive", 1
    )
    first = asyncio.create_task(
        controller.acquire("alice-1", "code-32k", "alice", "interactive", 1)
    )
    await asyncio.sleep(0.01)
    with pytest.raises(QueueFullError, match="principal queue"):
        await controller.acquire(
            "alice-2", "code-32k", "alice", "interactive", 1
        )
    await controller.release(blocker)
    reservation = await first
    await controller.release(reservation)


@pytest.mark.asyncio
async def test_one_active_request_per_principal(gateway_config) -> None:
    backend = gateway_config.backend_pools["code-default"].backends[0]
    backend.max_concurrent_requests = 2
    backend.max_long_requests = 1
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    controller = AdmissionController(registry, max_active_per_principal=1)

    alice_active = await controller.acquire(
        "alice-1", "code-32k", "alice", "interactive", 1
    )
    alice_waiting = asyncio.create_task(
        controller.acquire("alice-2", "code-32k", "alice", "interactive", 1)
    )
    bob_active = await controller.acquire(
        "bob-1", "code-32k", "bob", "interactive", 1
    )
    assert not alice_waiting.done()

    await controller.release(alice_active)
    await controller.release(bob_active)
    alice_second = await alice_waiting
    await controller.release(alice_second)


@pytest.mark.asyncio
async def test_waiting_long_request_does_not_block_interactive_capacity(
    gateway_config,
) -> None:
    backend = gateway_config.backend_pools["code-default"].backends[0]
    backend.max_concurrent_requests = 2
    backend.max_long_requests = 1
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    controller = AdmissionController(registry, long_queue_promote_seconds=0)

    active_long = await controller.acquire(
        "long-1", "code-32k", "alice", "long", 1
    )
    waiting_long = asyncio.create_task(
        controller.acquire("long-2", "code-32k", "bob", "long", 1)
    )
    await asyncio.sleep(0.01)
    interactive = await controller.acquire(
        "normal-1", "code-32k", "carol", "interactive", 1
    )
    assert not waiting_long.done()

    await controller.release(active_long)
    await controller.release(interactive)
    second_long = await waiting_long
    await controller.release(second_long)


@pytest.mark.asyncio
async def test_request_validated_before_reload_is_not_admitted(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    controller = AdmissionController(registry)
    validated_revision = registry.config_revision
    await controller.reload(gateway_config.model_copy(deep=True))

    with pytest.raises(ConfigurationChangedError):
        await controller.acquire(
            "stale", "code-32k", "alice", "interactive", 1,
            config_revision=validated_revision,
        )

    assert controller.queue_depths() == {}
    assert registry.snapshot()[0]["inflight"] == 0


@pytest.mark.asyncio
async def test_reload_invalidates_queued_requests(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    controller = AdmissionController(registry)
    blocker = await controller.acquire("active", "code-32k", "alice", "interactive", 1)
    waiting = asyncio.create_task(
        controller.acquire("waiting", "code-32k", "bob", "interactive", 1)
    )
    await asyncio.sleep(0)
    assert controller.queue_depths()["code-32k"]["interactive"] == 1

    await controller.reload(gateway_config.model_copy(deep=True))

    with pytest.raises(ConfigurationChangedError):
        await waiting
    assert controller.queue_depths() == {}
    await controller.release(blocker)
    assert registry.snapshot()[0]["inflight"] == 0


@pytest.mark.asyncio
async def test_expired_waiter_cannot_acquire_new_capacity(
    gateway_config, monkeypatch
) -> None:
    now = [0.0]
    monkeypatch.setattr("app.admission.monotonic", lambda: now[0])
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    controller = AdmissionController(registry)
    blocker = await controller.acquire("active", "code-32k", "alice", "interactive", 1)
    waiting = asyncio.create_task(
        controller.acquire("waiting", "code-32k", "bob", "interactive", 1)
    )
    await asyncio.sleep(0)
    now[0] = 2.0

    await controller.release(blocker)

    with pytest.raises(QueueTimeoutError):
        await waiting
    assert registry.snapshot()[0]["inflight"] == 0
    assert controller.queue_depths() == {}


@pytest.mark.asyncio
async def test_blocked_principal_does_not_promote_a_new_long_request(
    gateway_config, monkeypatch
) -> None:
    now = [0.0]
    monkeypatch.setattr("app.admission.monotonic", lambda: now[0])
    gateway_config.backend_pools["code-default"].backends[0].max_concurrent_requests = 2
    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    controller = AdmissionController(registry, long_queue_promote_seconds=120)
    alice_active = await controller.acquire(
        "alice-active", "code-32k", "alice", "interactive", 200
    )
    blocker = await controller.acquire(
        "active", "code-32k", "blocker", "interactive", 200
    )
    old_blocked = asyncio.create_task(
        controller.acquire("alice-old", "code-32k", "alice", "long", 200)
    )
    await asyncio.sleep(0)
    now[0] = 121.0
    new_long = asyncio.create_task(
        controller.acquire("bob-new", "code-32k", "bob", "long", 200)
    )
    interactive = asyncio.create_task(
        controller.acquire("carol", "code-32k", "carol", "interactive", 200)
    )
    await asyncio.sleep(0)

    await controller.release(blocker)
    carol = await asyncio.wait_for(interactive, timeout=1)

    assert not new_long.done()
    await controller.release(carol)
    bob = await new_long
    await controller.release(bob)
    await controller.release(alice_active)
    alice = await old_blocked
    await controller.release(alice)
