from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import BackendSpec, GatewayConfig, ModelRoute


@dataclass(slots=True)
class BackendRuntime:
    spec: BackendSpec
    healthy: bool = False
    inflight: int = 0
    long_inflight: int = 0
    last_check: datetime | None = None
    last_error: str | None = None


@dataclass(slots=True)
class Reservation:
    request_id: str
    model_alias: str
    principal_id: str
    route: ModelRoute
    backend: BackendSpec
    request_class: str
    released: bool = False


class BackendRegistry:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.config_revision = 0
        self._runtimes: dict[str, BackendRuntime] = {}
        self.reload(config)

    def reload(self, config: GatewayConfig) -> None:
        previous = self._runtimes
        current: dict[str, BackendRuntime] = {}
        new_ids = {
            backend.id
            for pool in config.backend_pools.values()
            for backend in pool.backends
        }
        active_removed = [
            backend_id
            for backend_id, runtime in previous.items()
            if backend_id not in new_ids and runtime.inflight > 0
        ]
        if active_removed:
            raise ValueError(
                "cannot remove backends with active requests: "
                + ", ".join(sorted(active_removed))
            )
        active_changed = [
            spec.id
            for pool in config.backend_pools.values()
            for spec in pool.backends
            if (runtime := previous.get(spec.id)) is not None
            and runtime.inflight > 0
            and (runtime.spec.url, runtime.spec.api_key_env)
            != (spec.url, spec.api_key_env)
        ]
        if active_changed:
            raise ValueError(
                "cannot change backend endpoints or credentials with active requests: "
                + ", ".join(sorted(active_changed))
            )
        for pool in config.backend_pools.values():
            for spec in pool.backends:
                old_runtime = previous.get(spec.id)
                runtime = BackendRuntime(spec=spec)
                if old_runtime is not None:
                    runtime.inflight = old_runtime.inflight
                    runtime.long_inflight = old_runtime.long_inflight
                    if (
                        old_runtime.spec.url,
                        old_runtime.spec.api_key_env,
                        old_runtime.spec.health_path,
                        old_runtime.spec.enabled,
                    ) == (spec.url, spec.api_key_env, spec.health_path, spec.enabled):
                        runtime.healthy = old_runtime.healthy
                        runtime.last_check = old_runtime.last_check
                        runtime.last_error = old_runtime.last_error
                current[spec.id] = runtime
        self.config = config
        self._runtimes = current
        self.config_revision += 1

    def resolve_model(self, alias: str) -> ModelRoute | None:
        return self.config.models.get(alias)

    def reserve(
        self,
        request_id: str,
        alias: str,
        principal_id: str,
        request_class: str,
    ) -> Reservation | None:
        route = self.resolve_model(alias)
        if route is None:
            return None
        pool = self.config.backend_pools[route.backend_pool]
        candidates: list[BackendRuntime] = []
        for spec in pool.backends:
            runtime = self._runtimes[spec.id]
            if not spec.enabled or not runtime.healthy:
                continue
            if spec.max_context_tokens < route.max_context_tokens:
                continue
            if runtime.inflight >= spec.max_concurrent_requests:
                continue
            if (
                request_class == "long"
                and runtime.long_inflight >= spec.max_long_requests
            ):
                continue
            candidates.append(runtime)
        if not candidates:
            return None

        def score(runtime: BackendRuntime) -> tuple[float, int, str]:
            utilization = runtime.inflight / runtime.spec.max_concurrent_requests
            return (utilization / runtime.spec.weight, runtime.inflight, runtime.spec.id)

        selected = min(candidates, key=score)
        selected.inflight += 1
        if request_class == "long":
            selected.long_inflight += 1
        return Reservation(
            request_id=request_id,
            model_alias=alias,
            principal_id=principal_id,
            route=route,
            backend=selected.spec,
            request_class=request_class,
        )

    def has_capacity(self, alias: str, request_class: str) -> bool:
        route = self.resolve_model(alias)
        if route is None:
            return False
        pool = self.config.backend_pools[route.backend_pool]
        for spec in pool.backends:
            runtime = self._runtimes[spec.id]
            if not spec.enabled or not runtime.healthy:
                continue
            if spec.max_context_tokens < route.max_context_tokens:
                continue
            if runtime.inflight >= spec.max_concurrent_requests:
                continue
            if (
                request_class == "long"
                and runtime.long_inflight >= spec.max_long_requests
            ):
                continue
            return True
        return False

    def release(self, reservation: Reservation) -> None:
        if reservation.released:
            return
        runtime = self._runtimes.get(reservation.backend.id)
        if runtime is not None:
            runtime.inflight = max(0, runtime.inflight - 1)
            if reservation.request_class == "long":
                runtime.long_inflight = max(0, runtime.long_inflight - 1)
        reservation.released = True

    def tokenizer_backend(self, alias: str) -> BackendSpec | None:
        route = self.resolve_model(alias)
        if route is None:
            return None
        pool = self.config.backend_pools[route.backend_pool]
        candidates = [
            self._runtimes[spec.id]
            for spec in pool.backends
            if spec.enabled
            and self._runtimes[spec.id].healthy
            and spec.max_context_tokens >= route.max_context_tokens
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda runtime: (runtime.inflight, runtime.spec.id),
        ).spec

    def mark_health(
        self,
        backend_id: str,
        healthy: bool,
        error: str | None = None,
        *,
        expected_backend: BackendSpec | None = None,
    ) -> bool:
        runtime = self._runtimes.get(backend_id)
        if runtime is None or (
            expected_backend is not None and runtime.spec is not expected_backend
        ):
            return False
        runtime.healthy = healthy
        runtime.last_check = datetime.now(UTC)
        runtime.last_error = error
        return True

    def has_healthy_backend(self) -> bool:
        return any(
            runtime.healthy and runtime.spec.enabled
            for runtime in self._runtimes.values()
        )

    def backends(self) -> list[BackendSpec]:
        return [runtime.spec for runtime in self._runtimes.values()]

    def api_key_for(self, backend: BackendSpec) -> str | None:
        if not backend.api_key_env:
            return None
        return os.getenv(backend.api_key_env)

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "id": runtime.spec.id,
                "host_id": runtime.spec.host_id,
                "url": runtime.spec.url,
                "enabled": runtime.spec.enabled,
                "healthy": runtime.healthy,
                "inflight": runtime.inflight,
                "long_inflight": runtime.long_inflight,
                "capacity": runtime.spec.max_concurrent_requests,
                "long_capacity": runtime.spec.max_long_requests,
                "max_context_tokens": runtime.spec.max_context_tokens,
                "last_check": runtime.last_check.isoformat()
                if runtime.last_check
                else None,
                "last_error": runtime.last_error,
            }
            for runtime in sorted(self._runtimes.values(), key=lambda item: item.spec.id)
        ]
