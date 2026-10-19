from __future__ import annotations

import asyncio

import httpx

from .admission import AdmissionController
from .config import BackendSpec
from .metrics import GatewayMetrics
from .registry import BackendRegistry


class BackendHealthMonitor:
    def __init__(
        self,
        controller: AdmissionController,
        registry: BackendRegistry,
        client: httpx.AsyncClient,
        metrics: GatewayMetrics,
        *,
        interval_seconds: float,
        timeout_seconds: float,
    ) -> None:
        self.controller = controller
        self.registry = registry
        self.client = client
        self.metrics = metrics
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds
        self._metric_backend_ids: set[str] = set()

    async def check_once(self) -> None:
        await asyncio.gather(
            *(self._check(backend) for backend in self.registry.backends())
        )
        self.refresh_runtime_metrics()

    async def _check(self, backend: BackendSpec) -> None:
        if not backend.enabled:
            await self.controller.mark_health(
                backend.id, False, "disabled", expected_backend=backend
            )
            return
        headers: dict[str, str] = {}
        api_key = self.registry.api_key_for(backend)
        if backend.api_key_env and not api_key:
            error = f"required environment variable {backend.api_key_env} is missing"
            await self.controller.mark_health(
                backend.id, False, error, expected_backend=backend
            )
            return
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            response = await self.client.get(
                f"{backend.url}{backend.health_path}",
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except Exception as exc:
            await self.controller.mark_health(
                backend.id, False, str(exc)[:500], expected_backend=backend
            )
        else:
            await self.controller.mark_health(
                backend.id, True, expected_backend=backend
            )

    def refresh_runtime_metrics(self) -> None:
        snapshot = self.registry.snapshot()
        current_ids = {item["id"] for item in snapshot}
        for backend_id in self._metric_backend_ids - current_ids:
            self.metrics.backend_health.remove(backend_id)
            self.metrics.backend_enabled.remove(backend_id)
            self.metrics.backend_inflight.remove(backend_id)
        for item in snapshot:
            self.metrics.backend_enabled.labels(backend=item["id"]).set(
                1 if item["enabled"] else 0
            )
            self.metrics.backend_health.labels(backend=item["id"]).set(
                1 if item["healthy"] else 0
            )
            self.metrics.backend_inflight.labels(backend=item["id"]).set(
                item["inflight"]
            )
        self._metric_backend_ids = current_ids

    async def run(self) -> None:
        while True:
            await self.check_once()
            await asyncio.sleep(self.interval_seconds)
