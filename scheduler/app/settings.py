from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class Settings:
    backends_config: str = "/app/config/backends.yaml"
    internal_api_key: str = ""
    queue_timeout_seconds: float = 600.0
    request_timeout_seconds: float = 1260.0
    max_queue_depth: int = 50
    max_queued_per_principal: int = 2
    max_active_per_principal: int = 1
    long_queue_promote_seconds: float = 120.0
    health_interval_seconds: float = 10.0
    health_timeout_seconds: float = 3.0
    tokenize_timeout_seconds: float = 10.0
    upstream_connect_timeout_seconds: float = 10.0
    upstream_read_timeout_seconds: float = 600.0
    upstream_write_timeout_seconds: float = 30.0
    upstream_pool_timeout_seconds: float = 10.0
    max_request_body_bytes: int = 8 * 1024 * 1024
    start_health_monitor: bool = True

    def __post_init__(self) -> None:
        for name in (
            "queue_timeout_seconds",
            "request_timeout_seconds",
            "health_interval_seconds",
            "health_timeout_seconds",
            "tokenize_timeout_seconds",
            "upstream_connect_timeout_seconds",
            "upstream_read_timeout_seconds",
            "upstream_write_timeout_seconds",
            "upstream_pool_timeout_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.queue_timeout_seconds >= self.request_timeout_seconds:
            raise ValueError("queue_timeout_seconds must be less than request_timeout_seconds")
        if (
            isinstance(self.long_queue_promote_seconds, bool)
            or not math.isfinite(self.long_queue_promote_seconds)
            or self.long_queue_promote_seconds < 0
        ):
            raise ValueError("long_queue_promote_seconds must be finite and non-negative")
        for name in (
            "max_queue_depth",
            "max_queued_per_principal",
            "max_active_per_principal",
            "max_request_body_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        local_config = Path(__file__).resolve().parents[2] / "config" / "backends.yaml"
        return cls(
            backends_config=os.getenv("BACKENDS_CONFIG", str(local_config)),
            internal_api_key=os.getenv("SCHEDULER_INTERNAL_API_KEY", ""),
            queue_timeout_seconds=float(
                os.getenv("QUEUE_TIMEOUT_SECONDS", str(defaults.queue_timeout_seconds))
            ),
            request_timeout_seconds=float(
                os.getenv("REQUEST_TIMEOUT_SECONDS", str(defaults.request_timeout_seconds))
            ),
            max_queue_depth=int(
                os.getenv("MAX_QUEUE_DEPTH", str(defaults.max_queue_depth))
            ),
            max_queued_per_principal=int(
                os.getenv(
                    "MAX_QUEUED_PER_PRINCIPAL",
                    str(defaults.max_queued_per_principal),
                )
            ),
            max_active_per_principal=int(
                os.getenv(
                    "MAX_ACTIVE_PER_PRINCIPAL",
                    str(defaults.max_active_per_principal),
                )
            ),
            long_queue_promote_seconds=float(
                os.getenv(
                    "LONG_QUEUE_PROMOTE_SECONDS",
                    str(defaults.long_queue_promote_seconds),
                )
            ),
            health_interval_seconds=float(
                os.getenv("HEALTH_INTERVAL_SECONDS", str(defaults.health_interval_seconds))
            ),
            health_timeout_seconds=float(
                os.getenv("HEALTH_TIMEOUT_SECONDS", str(defaults.health_timeout_seconds))
            ),
            tokenize_timeout_seconds=float(
                os.getenv(
                    "TOKENIZE_TIMEOUT_SECONDS",
                    str(defaults.tokenize_timeout_seconds),
                )
            ),
            upstream_connect_timeout_seconds=float(
                os.getenv(
                    "UPSTREAM_CONNECT_TIMEOUT_SECONDS",
                    str(defaults.upstream_connect_timeout_seconds),
                )
            ),
            upstream_read_timeout_seconds=float(
                os.getenv(
                    "UPSTREAM_READ_TIMEOUT_SECONDS",
                    str(defaults.upstream_read_timeout_seconds),
                )
            ),
            upstream_write_timeout_seconds=float(
                os.getenv(
                    "UPSTREAM_WRITE_TIMEOUT_SECONDS",
                    str(defaults.upstream_write_timeout_seconds),
                )
            ),
            upstream_pool_timeout_seconds=float(
                os.getenv(
                    "UPSTREAM_POOL_TIMEOUT_SECONDS",
                    str(defaults.upstream_pool_timeout_seconds),
                )
            ),
            max_request_body_bytes=int(
                os.getenv("MAX_REQUEST_BODY_BYTES", str(defaults.max_request_body_bytes))
            ),
            start_health_monitor=_env_bool("START_HEALTH_MONITOR", True),
        )
