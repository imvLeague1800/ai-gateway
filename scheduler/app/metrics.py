from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest


# Preserve short-request resolution while covering queue and generation deadlines.
LATENCY_BUCKETS = Histogram.DEFAULT_BUCKETS[:-1] + (30, 60, 120, 300, 600, 1200)


class GatewayMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "model_gateway_requests_total",
            "Completed or rejected scheduler requests",
            ["model", "request_class", "status", "backend"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "model_gateway_request_duration_seconds",
            "Scheduler request duration including queue time",
            ["model", "backend"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.queue_depth = Gauge(
            "model_gateway_queue_depth",
            "Number of requests waiting for admission",
            ["model", "request_class"],
            registry=self.registry,
        )
        self.queue_wait = Histogram(
            "model_gateway_queue_wait_seconds",
            "Time spent waiting for admission",
            ["model", "request_class"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.prompt_tokens = Histogram(
            "model_gateway_prompt_tokens",
            "Exact prompt tokens counted before admission",
            ["model", "request_class"],
            buckets=(256, 1024, 2048, 4096, 8192, 16384, 24576, 32768),
            registry=self.registry,
        )
        self.backend_health = Gauge(
            "model_gateway_backend_healthy",
            "Backend health state (1 healthy, 0 unhealthy)",
            ["backend"],
            registry=self.registry,
        )
        self.backend_enabled = Gauge(
            "model_gateway_backend_enabled",
            "Backend configuration state (1 enabled, 0 disabled)",
            ["backend"],
            registry=self.registry,
        )
        self.backend_inflight = Gauge(
            "model_gateway_backend_inflight",
            "Requests currently assigned to a backend",
            ["backend"],
            registry=self.registry,
        )

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
