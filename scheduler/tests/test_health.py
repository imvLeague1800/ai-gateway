from __future__ import annotations

import asyncio

import httpx
import pytest

from app.admission import AdmissionController
from app.health import BackendHealthMonitor
from app.metrics import GatewayMetrics
from app.registry import BackendRegistry


@pytest.mark.asyncio
async def test_old_endpoint_health_probe_cannot_mark_replacement_healthy(
    gateway_config,
) -> None:
    probe_started = asyncio.Event()
    finish_probe = asyncio.Event()

    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "backend-1"
        probe_started.set()
        await finish_probe.wait()
        return httpx.Response(200)

    registry = BackendRegistry(gateway_config)
    controller = AdmissionController(registry)
    metrics = GatewayMetrics()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monitor = BackendHealthMonitor(
            controller, registry, client, metrics, interval_seconds=1, timeout_seconds=1
        )
        probe = asyncio.create_task(monitor.check_once())
        await probe_started.wait()
        replacement = gateway_config.model_copy(deep=True)
        replacement.backend_pools["code-default"].backends[0].url = "http://new:8000"
        await controller.reload(replacement)
        await controller.mark_health("backend-1", False, "new endpoint unavailable")
        finish_probe.set()
        await probe

    assert not registry.has_healthy_backend()
    assert registry.snapshot()[0]["last_error"] == "new endpoint unavailable"
    payload, _ = metrics.render()
    assert b'model_gateway_backend_healthy{backend="backend-1"} 0.0' in payload


@pytest.mark.asyncio
async def test_backend_metric_labels_track_disabled_and_removed_backends(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    controller = AdmissionController(registry)
    metrics = GatewayMetrics()
    client = httpx.AsyncClient()
    monitor = BackendHealthMonitor(
        controller, registry, client, metrics, interval_seconds=1, timeout_seconds=1
    )
    monitor.refresh_runtime_metrics()
    payload, _ = metrics.render()
    assert b'model_gateway_backend_enabled{backend="backend-1"} 1.0' in payload

    disabled = gateway_config.model_copy(deep=True)
    disabled.backend_pools["code-default"].backends[0].enabled = False
    registry.reload(disabled)
    monitor.refresh_runtime_metrics()
    payload, _ = metrics.render()
    assert b'model_gateway_backend_enabled{backend="backend-1"} 0.0' in payload

    replacement = gateway_config.model_copy(deep=True)
    replacement.backend_pools["code-default"].backends[0].id = "backend-2"
    registry.reload(replacement)
    monitor.refresh_runtime_metrics()
    payload, _ = metrics.render()
    assert b'backend="backend-1"' not in payload
    assert b'model_gateway_backend_enabled{backend="backend-2"} 1.0' in payload
    await client.aclose()
