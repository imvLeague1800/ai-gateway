from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import anyio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .admission import (
    AdmissionController,
    ConfigurationChangedError,
    QueueFullError,
    QueueTimeoutError,
)
from .config import GatewayConfig, load_gateway_config
from .errors import openai_error
from .health import BackendHealthMonitor
from .lifecycle import RequestLifecycleMiddleware, TERMINATION_REASON
from .metrics import GatewayMetrics
from .registry import BackendRegistry, Reservation
from .settings import Settings
from .tokens import TokenizationError, VllmTokenCounter

logger = logging.getLogger("model_gateway.scheduler")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def create_app(
    *,
    settings: Settings | None = None,
    gateway_config: GatewayConfig | None = None,
    upstream_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    config = gateway_config or load_gateway_config(settings.backends_config)
    registry = BackendRegistry(config)
    controller = AdmissionController(
        registry,
        max_queue_depth=settings.max_queue_depth,
        max_queued_per_principal=settings.max_queued_per_principal,
        max_active_per_principal=settings.max_active_per_principal,
        long_queue_promote_seconds=settings.long_queue_promote_seconds,
    )
    metrics = GatewayMetrics()
    upstream_timeout = httpx.Timeout(
        connect=settings.upstream_connect_timeout_seconds,
        read=settings.upstream_read_timeout_seconds,
        write=settings.upstream_write_timeout_seconds,
        pool=settings.upstream_pool_timeout_seconds,
    )
    client = upstream_client or httpx.AsyncClient(timeout=upstream_timeout)
    owns_client = upstream_client is None
    token_counter = VllmTokenCounter(
        registry,
        client,
        timeout_seconds=settings.tokenize_timeout_seconds,
    )
    monitor = BackendHealthMonitor(
        controller,
        registry,
        client,
        metrics,
        interval_seconds=settings.health_interval_seconds,
        timeout_seconds=settings.health_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        monitor_task: asyncio.Task[None] | None = None
        if settings.start_health_monitor:
            monitor_task = asyncio.create_task(monitor.run(), name="backend-health-monitor")
        try:
            yield
        finally:
            if monitor_task is not None:
                monitor_task.cancel()
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass
            if owns_client:
                await client.aclose()

    app = FastAPI(
        title="Model Gateway Admission Scheduler",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.registry = registry
    app.state.controller = controller
    app.state.monitor = monitor
    app.add_middleware(
        RequestLifecycleMiddleware, timeout_seconds=settings.request_timeout_seconds
    )

    def authorized(request: Request) -> bool:
        expected = settings.internal_api_key
        if not expected:
            return False
        authorization = request.headers.get("authorization", "")
        scheme, _, credential = authorization.partition(" ")
        return scheme.lower() == "bearer" and secrets.compare_digest(
            credential.encode("utf-8"), expected.encode("utf-8")
        )

    def require_internal(request: Request) -> JSONResponse | None:
        if not settings.internal_api_key:
            return openai_error(
                503,
                "Scheduler internal authentication is not configured.",
                error_type="server_error",
                code="internal_auth_not_configured",
            )
        if not authorized(request):
            return openai_error(
                401,
                "Invalid internal gateway credential.",
                error_type="authentication_error",
                code="invalid_api_key",
            )
        return None

    def refresh_queue_metrics() -> None:
        depths = controller.queue_depths()
        for alias in registry.config.models:
            for request_class in ("interactive", "long"):
                metrics.queue_depth.labels(
                    model=alias, request_class=request_class
                ).set(depths.get(alias, {}).get(request_class, 0))

    def refresh_backend_metrics() -> None:
        monitor.refresh_runtime_metrics()

    def record_result(
        model: str,
        request_class: str,
        status: str,
        backend: str,
        started: float,
    ) -> None:
        metrics.requests.labels(
            model=model,
            request_class=request_class,
            status=status,
            backend=backend,
        ).inc()
        metrics.duration.labels(model=model, backend=backend).observe(
            max(0.0, time.monotonic() - started)
        )
        refresh_backend_metrics()

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness() -> Response:
        if registry.has_healthy_backend():
            return JSONResponse({"status": "ready"})
        return JSONResponse(
            {"status": "not_ready", "reason": "no healthy backend"},
            status_code=503,
        )

    @app.get("/metrics")
    async def prometheus_metrics() -> Response:
        refresh_queue_metrics()
        refresh_backend_metrics()
        payload, media_type = metrics.render()
        return Response(content=payload, media_type=media_type)

    @app.get("/v1/models")
    async def models(request: Request) -> Response:
        auth_error = require_internal(request)
        if auth_error:
            return auth_error
        created = int(time.time())
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": alias,
                        "object": "model",
                        "created": created,
                        "owned_by": "internal-model-gateway",
                    }
                    for alias in sorted(registry.config.models)
                ],
            }
        )

    @app.get("/admin/backends")
    async def backend_status(request: Request) -> Response:
        auth_error = require_internal(request)
        if auth_error:
            return auth_error
        return JSONResponse(
            {
                "backends": registry.snapshot(),
                "queues": controller.queue_depths(),
            }
        )

    @app.post("/admin/reload")
    async def reload_config(request: Request) -> Response:
        auth_error = require_internal(request)
        if auth_error:
            return auth_error
        try:
            new_config = load_gateway_config(settings.backends_config)
            await controller.reload(new_config)
            await monitor.check_once()
        except Exception as exc:
            logger.exception("gateway configuration reload failed")
            return JSONResponse(
                {"status": "rejected", "error": str(exc)}, status_code=400
            )
        return JSONResponse({"status": "reloaded", "backends": registry.snapshot()})

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        started = time.monotonic()
        auth_error = require_internal(request)
        if auth_error:
            return auth_error

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared_length = int(content_length)
                if declared_length < 0:
                    raise ValueError("negative content length")
                if declared_length > settings.max_request_body_bytes:
                    return openai_error(
                        413,
                        "Request body exceeds the gateway limit.",
                        code="request_too_large",
                    )
            except ValueError:
                return openai_error(400, "Invalid Content-Length header.")

        raw_body = bytearray()
        async for chunk in request.stream():
            if len(raw_body) + len(chunk) > settings.max_request_body_bytes:
                return openai_error(
                    413,
                    "Request body exceeds the gateway limit.",
                    code="request_too_large",
                )
            raw_body.extend(chunk)
        try:
            body = json.loads(
                raw_body,
                parse_constant=_reject_json_constant,
                parse_float=_finite_json_float,
            )
        except (ValueError, UnicodeDecodeError, RecursionError):
            return openai_error(400, "Request body must be valid JSON.")
        if not isinstance(body, dict):
            return openai_error(400, "Request body must be a JSON object.")

        requested_model = body.get("model")
        if not isinstance(requested_model, str) or not requested_model:
            return openai_error(400, "A model alias is required.", param="model")
        config_revision = registry.config_revision
        model_alias = requested_model
        route = registry.resolve_model(model_alias)
        if route is None and requested_model.startswith("openai/"):
            model_alias = requested_model.removeprefix("openai/")
            route = registry.resolve_model(model_alias)
        if route is None:
            record_result(
                "unknown", "unknown", "rejected_unknown_model", "none", started
            )
            return openai_error(
                404,
                f"Unknown model alias: {model_alias}",
                code="model_not_found",
                param="model",
            )
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            record_result(
                model_alias,
                "unknown",
                "rejected_invalid_messages",
                "none",
                started,
            )
            return openai_error(
                400, "messages must be a non-empty array.", param="messages"
            )
        principal_id = body.get("user")
        if not isinstance(principal_id, str) or not principal_id or len(principal_id) > 128:
            record_result(
                model_alias, "unknown", "rejected_missing_principal", "none", started
            )
            return openai_error(
                400,
                "Trusted principal identity is missing. Send requests through LiteLLM.",
                code="principal_identity_missing",
                param="user",
            )
        stream_value = body.get("stream", False)
        if not isinstance(stream_value, bool):
            return openai_error(400, "stream must be a boolean.", param="stream")
        stream_options = body.get("stream_options")
        if stream_options is not None and not isinstance(stream_options, dict):
            return openai_error(
                400, "stream_options must be a JSON object.", param="stream_options"
            )
        n_value = body.get("n", 1)
        if isinstance(n_value, bool) or not isinstance(n_value, int) or n_value != 1:
            return openai_error(
                400,
                "The gateway supports exactly one generated choice per request.",
                param="n",
                code="unsupported_choice_count",
            )

        output_value = body.get(
            "max_completion_tokens",
            body.get("max_tokens", route.default_output_tokens),
        )
        if isinstance(output_value, bool) or not isinstance(output_value, int):
            return openai_error(
                400, "max_tokens must be an integer.", param="max_tokens"
            )
        if output_value <= 0 or output_value > route.max_output_tokens:
            record_result(
                model_alias, "unknown", "rejected_output_limit", "none", started
            )
            return openai_error(
                400,
                f"Requested output must be between 1 and {route.max_output_tokens} tokens.",
                code="output_limit_exceeded",
                param="max_tokens",
            )

        try:
            token_count = await token_counter.count(model_alias, body)
        except TokenizationError as exc:
            logger.warning("tokenization failed model=%s error=%s", model_alias, exc)
            record_result(
                model_alias, "unknown", "tokenization_unavailable", "none", started
            )
            return openai_error(
                503,
                "The model tokenizer is temporarily unavailable.",
                error_type="server_error",
                code="tokenizer_unavailable",
            )

        prompt_tokens = token_count.prompt_tokens
        request_class = route.request_class(prompt_tokens)
        metrics.prompt_tokens.labels(
            model=model_alias, request_class=request_class
        ).observe(prompt_tokens)
        if token_count.backend_max_model_len < route.max_context_tokens:
            record_result(
                model_alias,
                request_class,
                "backend_context_mismatch",
                "none",
                started,
            )
            return openai_error(
                503,
                "The backend context limit is lower than the gateway configuration.",
                error_type="server_error",
                code="backend_context_mismatch",
            )
        if prompt_tokens + output_value > route.max_context_tokens:
            record_result(
                model_alias,
                request_class,
                "rejected_context_limit",
                "none",
                started,
            )
            return openai_error(
                400,
                (
                    "Prompt plus requested output exceeds the "
                    f"{route.max_context_tokens}-token model limit."
                ),
                code="context_length_exceeded",
                param="messages",
            )

        request_id = str(uuid.uuid4())
        refresh_queue_metrics()
        queue_started = time.monotonic()
        try:
            reservation = await controller.acquire(
                request_id,
                model_alias,
                principal_id,
                request_class,
                settings.queue_timeout_seconds,
                config_revision=config_revision,
            )
        except ConfigurationChangedError:
            return openai_error(
                503,
                "Model configuration changed while preparing this request. Retry the request.",
                error_type="server_error",
                code="configuration_changed",
                headers={"Retry-After": "1"},
            )
        except QueueFullError:
            refresh_queue_metrics()
            record_result(
                model_alias, request_class, "queue_full", "none", started
            )
            return openai_error(
                429,
                "The model queue is full for this user or model.",
                error_type="rate_limit_error",
                code="queue_full",
                headers={"Retry-After": "30"},
            )
        except QueueTimeoutError:
            refresh_queue_metrics()
            record_result(
                model_alias, request_class, "queue_timeout", "none", started
            )
            return openai_error(
                429,
                "No model backend became available before the queue deadline.",
                error_type="rate_limit_error",
                code="queue_timeout",
                headers={"Retry-After": "30"},
            )
        upstream: httpx.Response | None = None
        queue_wait_seconds = max(0.0, time.monotonic() - queue_started)
        logger.info(json.dumps({
            "event": "scheduler_request_admitted", "request_id": request_id,
            "model": model_alias, "backend_id": reservation.backend.id,
            "principal_id": principal_id,
            "queue_wait_seconds": round(queue_wait_seconds, 6),
        }, separators=(",", ":")))
        handed_off = False
        finished = False
        status = "cancelled"

        async def cleanup() -> None:
            nonlocal finished, status
            if finished:
                return
            finished = True
            status = request.scope.get(TERMINATION_REASON, status)
            # Starlette cancels its stream task on disconnect. Shield cleanup so
            # the upstream connection and both concurrency counters are released.
            with anyio.CancelScope(shield=True):
                try:
                    if upstream is not None:
                        with anyio.move_on_after(settings.upstream_connect_timeout_seconds):
                            await upstream.aclose()
                except Exception:
                    logger.exception("upstream close failed request_id=%s", request_id)
                finally:
                    await controller.release(reservation)
            record_result(
                model_alias, request_class, status, reservation.backend.id, started
            )
            logger.info(_metadata_event(request_id, model_alias, reservation, status,
                                        started, principal_id, prompt_tokens))

        try:
            metrics.queue_wait.labels(
                model=model_alias, request_class=request_class
            ).observe(max(0.0, time.monotonic() - queue_started))
            refresh_queue_metrics()
            refresh_backend_metrics()

            upstream_body = dict(body)
            upstream_body["model"] = reservation.route.upstream_model
            if (
                "max_tokens" not in upstream_body
                and "max_completion_tokens" not in upstream_body
            ):
                upstream_body["max_tokens"] = output_value
            if stream_value:
                upstream_body["stream_options"] = {
                    **(stream_options or {}),
                    "include_usage": True,
                }

            upstream_headers = {
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream_value else "application/json",
                "X-Request-ID": request_id,
            }
            backend_api_key = registry.api_key_for(reservation.backend)
            if backend_api_key:
                upstream_headers["Authorization"] = f"Bearer {backend_api_key}"

            upstream_request = client.build_request(
                "POST",
                f"{reservation.backend.url}/v1/chat/completions",
                headers=upstream_headers,
                json=upstream_body,
                timeout=upstream_timeout,
            )
            upstream = await client.send(upstream_request, stream=True)
            response_headers = _response_headers(
                upstream.headers, request_id, request_class, prompt_tokens
            )
            response_headers["x-gateway-backend-id"] = reservation.backend.id
            response_headers["x-gateway-queue-wait-seconds"] = str(round(queue_wait_seconds, 6))
            if not stream_value or upstream.status_code >= 400:
                content = await upstream.aread()
                status = "success" if upstream.status_code < 400 else "backend_error"
                return Response(
                    content=content,
                    status_code=upstream.status_code,
                    headers=response_headers,
                )

            async def stream_response() -> AsyncIterator[bytes]:
                nonlocal status
                try:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
                    status = "success"
                except asyncio.CancelledError:
                    status = "cancelled"
                    raise
                except Exception:
                    status = "stream_error"
                    logger.exception("upstream stream failed request_id=%s", request_id)
                    raise

            response = _ManagedStreamingResponse(
                stream_response(),
                cleanup=cleanup,
                status_code=upstream.status_code,
                headers=response_headers,
                media_type=upstream.headers.get("content-type", "text/event-stream"),
            )
            handed_off = True
            return response
        except httpx.RequestError as exc:
            status = "backend_connection_error"
            await controller.mark_health(
                reservation.backend.id,
                False,
                str(exc)[:500],
                expected_backend=reservation.backend,
            )
            return openai_error(
                502,
                "The selected model backend could not complete the request.",
                error_type="server_error",
                code="backend_unavailable",
            )
        finally:
            if not handed_off:
                await cleanup()

    return app


class _ManagedStreamingResponse(StreamingResponse):
    """Release admission even if sending headers fails before iteration starts."""

    def __init__(
        self, *args: Any, cleanup: Callable[[], Awaitable[None]], **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.cleanup = cleanup

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.cleanup()


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"Non-finite JSON number: {value}")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON number exceeds finite range")
    return number


def _response_headers(
    headers: httpx.Headers,
    request_id: str,
    request_class: str,
    prompt_tokens: int,
) -> dict[str, str]:
    allowed = {"content-type", "cache-control"}
    result = {key: value for key, value in headers.items() if key.lower() in allowed}
    result["x-gateway-request-id"] = request_id
    result["x-gateway-request-class"] = request_class
    result["x-gateway-prompt-tokens"] = str(prompt_tokens)
    return result


def _metadata_event(
    request_id: str,
    model_alias: str,
    reservation: Reservation,
    status: str,
    started: float,
    principal_id: str,
    prompt_tokens: int,
) -> str:
    return json.dumps(
        {
            "event": "scheduler_request_finished",
            "request_id": request_id,
            "model": model_alias,
            "backend_id": reservation.backend.id,
            "principal_id": principal_id,
            "request_class": reservation.request_class,
            "status": status,
            "prompt_tokens": prompt_tokens,
            "duration_seconds": round(max(0.0, time.monotonic() - started), 6),
        },
        separators=(",", ":"),
    )


app = create_app()
