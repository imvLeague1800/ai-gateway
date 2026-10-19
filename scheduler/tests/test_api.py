from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from conftest import AsyncChunks


def chat_body(**overrides) -> dict:
    return {
        "model": "code-32k",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 16,
        "user": "key-hash-a",
        **overrides,
    }


AUTH_HEADERS = {"Authorization": "Bearer scheduler-secret"}


def test_requires_internal_auth(gateway_config, test_settings) -> None:
    app = create_app(settings=test_settings, gateway_config=gateway_config)
    with TestClient(app) as client:
        response = client.get("/v1/models")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_invalid_non_ascii_credential_is_unauthorized(gateway_config, test_settings) -> None:
    app = create_app(settings=test_settings, gateway_config=gateway_config)
    with TestClient(app) as client:
        response = client.get(
            "/v1/models", headers=[(b"authorization", b"Bearer \xff")]
        )
    assert response.status_code == 401


def test_requires_trusted_litellm_principal(gateway_config, test_settings) -> None:
    app = create_app(settings=test_settings, gateway_config=gateway_config)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer scheduler-secret"},
            json={
                "model": "code-32k",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "principal_identity_missing"


def test_non_streaming_request_is_rewritten_and_forwarded(
    gateway_config, test_settings
) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tokenize":
            token_body = json.loads(request.content)
            assert token_body["model"] == "served-model"
            assert token_body["messages"][0]["content"] == "hello"
            return httpx.Response(
                200, json={"count": 5, "max_model_len": 1024, "tokens": [1] * 5}
            )
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": "served-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            },
            headers={"content-type": "application/json"},
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(
        settings=test_settings,
        gateway_config=gateway_config,
        upstream_client=upstream,
    )
    app.state.registry.mark_health("backend-1", True)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer scheduler-secret"},
            json={
                "model": "code-32k",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 16,
                "user": "key-hash-a",
            },
        )
    assert response.status_code == 200
    assert captured["model"] == "served-model"
    assert response.json()["choices"][0]["message"]["content"] == "OK"
    assert response.headers["x-gateway-request-id"]
    assert response.headers["x-gateway-backend-id"] == "backend-1"
    assert float(response.headers["x-gateway-queue-wait-seconds"]) >= 0
    assert app.state.registry.snapshot()[0]["inflight"] == 0


def test_streaming_response_is_passed_through(gateway_config, test_settings) -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"content":"O"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":"K"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tokenize":
            return httpx.Response(
                200, json={"count": 5, "max_model_len": 1024, "tokens": [1] * 5}
            )
        body = json.loads(request.content)
        assert body["stream_options"]["include_usage"] is True
        return httpx.Response(
            200,
            stream=AsyncChunks(chunks),
            headers={"content-type": "text/event-stream"},
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(
        settings=test_settings,
        gateway_config=gateway_config,
        upstream_client=upstream,
    )
    app.state.registry.mark_health("backend-1", True)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer scheduler-secret"},
            json={
                "model": "code-32k",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
                "max_tokens": 16,
                "user": "key-hash-a",
            },
        )
    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert app.state.registry.snapshot()[0]["inflight"] == 0


def test_rejects_context_before_backend(gateway_config, test_settings) -> None:
    gateway_config.models["code-32k"].max_context_tokens = 100
    gateway_config.models["code-32k"].long_request_threshold_tokens = 64

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tokenize"
        return httpx.Response(
            200, json={"count": 90, "max_model_len": 100, "tokens": [1] * 90}
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(
        settings=test_settings,
        gateway_config=gateway_config,
        upstream_client=upstream,
    )
    app.state.registry.mark_health("backend-1", True)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer scheduler-secret"},
            json={
                "model": "code-32k",
                "messages": [{"role": "user", "content": "x" * 300}],
                "max_tokens": 16,
                "user": "key-hash-a",
            },
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "context_length_exceeded"


def test_rejects_multiple_choices(gateway_config, test_settings) -> None:
    app = create_app(settings=test_settings, gateway_config=gateway_config)
    app.state.registry.mark_health("backend-1", True)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer scheduler-secret"},
            json={
                "model": "code-32k",
                "messages": [{"role": "user", "content": "hello"}],
                "n": 2,
                "user": "key-hash-a",
            },
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_choice_count"


@pytest.mark.parametrize("options", ["invalid", [1], 1, True])
def test_rejects_invalid_stream_options_before_tokenization(
    gateway_config, test_settings, options
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("invalid request must not reach an upstream")

    app = create_app(
        settings=test_settings,
        gateway_config=gateway_config,
        upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    app.state.registry.mark_health("backend-1", True)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            json=chat_body(stream=True, stream_options=options),
        )
    assert response.status_code == 400
    assert response.json()["error"]["param"] == "stream_options"
    assert app.state.registry.snapshot()[0]["inflight"] == 0


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_rejects_non_finite_json(gateway_config, test_settings, number) -> None:
    app = create_app(settings=test_settings, gateway_config=gateway_config)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            content=json.dumps(chat_body())[:-1] + ', "temperature": ' + number + "}",
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_chunked_request_stops_reading_at_size_limit(
    gateway_config, test_settings
) -> None:
    app = create_app(
        settings=replace(test_settings, max_request_body_bytes=16),
        gateway_config=gateway_config,
    )

    async def chunks():
        yield b"x" * 16
        yield b"x"
        pytest.fail("gateway continued buffering an oversized request")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://gateway"
    ) as client:
        response = await client.post(
            "/v1/chat/completions", headers=AUTH_HEADERS, content=chunks()
        )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_cancelled_upstream_send_releases_reservation(
    gateway_config, test_settings
) -> None:
    sending = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 5, "max_model_len": 1024})
        sending.set()
        await asyncio.Future()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(
            settings=test_settings, gateway_config=gateway_config, upstream_client=upstream
        )
        app.state.registry.mark_health("backend-1", True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            task = asyncio.create_task(client.post(
                "/v1/chat/completions", headers=AUTH_HEADERS, json=chat_body()
            ))
            await asyncio.wait_for(sending.wait(), timeout=1)
            assert app.state.registry.snapshot()[0]["inflight"] == 1
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert app.state.registry.snapshot()[0]["inflight"] == 0
        assert not app.state.controller._active_by_principal


@pytest.mark.parametrize("streaming", [False, True])
def test_upstream_read_and_close_failures_release_reservation(
    gateway_config, test_settings, streaming
) -> None:
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial response"
            raise httpx.ReadError("connection dropped")

        async def aclose(self):
            raise RuntimeError("close failed")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 5, "max_model_len": 1024})
        return httpx.Response(200, stream=BrokenStream())

    app = create_app(
        settings=test_settings,
        gateway_config=gateway_config,
        upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    app.state.registry.mark_health("backend-1", True)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH_HEADERS,
            json=chat_body(stream=streaming),
        )
    if not streaming:
        assert response.status_code == 502
    assert app.state.registry.snapshot()[0]["inflight"] == 0
    assert not app.state.controller._active_by_principal


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect_at_headers", [False, True])
async def test_stream_disconnect_releases_reservation(
    gateway_config, test_settings, disconnect_at_headers
) -> None:
    disconnected = asyncio.Event()
    closed = asyncio.Event()

    class WaitingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: hello\n\n"
            await asyncio.Future()

        async def aclose(self):
            await asyncio.sleep(0)
            closed.set()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 5, "max_model_len": 1024})
        return httpx.Response(200, stream=WaitingStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(
            settings=test_settings, gateway_config=gateway_config, upstream_client=upstream
        )
        app.state.registry.mark_health("backend-1", True)
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": json.dumps(chat_body(stream=True)).encode()}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if disconnect_at_headers and message["type"] == "http.response.start":
                raise OSError("client disconnected while sending headers")
            if message["type"] == "http.response.body":
                disconnected.set()

        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
            "query_string": b"", "headers": [(b"authorization", b"Bearer scheduler-secret")],
            "server": ("gateway", 80), "client": ("test", 123), "root_path": "",
        }
        if disconnect_at_headers:
            with pytest.raises(Exception):
                await asyncio.wait_for(app(scope, receive, send), timeout=1)
        else:
            await asyncio.wait_for(app(scope, receive, send), timeout=1)
        assert closed.is_set()
        assert app.state.registry.snapshot()[0]["inflight"] == 0
        assert not app.state.controller._active_by_principal


def test_applies_configured_upstream_timeouts(gateway_config, test_settings) -> None:
    settings = replace(
        test_settings, upstream_connect_timeout_seconds=2,
        upstream_read_timeout_seconds=3, upstream_write_timeout_seconds=4,
        upstream_pool_timeout_seconds=5,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 5, "max_model_len": 1024})
        assert request.extensions["timeout"] == {"connect": 2, "read": 3, "write": 4, "pool": 5}
        return httpx.Response(200, json={"choices": []})

    app = create_app(
        settings=settings, gateway_config=gateway_config,
        upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    app.state.registry.mark_health("backend-1", True)
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", headers=AUTH_HEADERS, json=chat_body())
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_reload_during_tokenization_requires_retry(gateway_config, test_settings) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tokenize"
        new_config = gateway_config.model_copy(deep=True)
        new_config.models["code-32k"].upstream_model = "replacement-model"
        await app.state.controller.reload(new_config)
        return httpx.Response(200, json={"count": 5, "max_model_len": 1024})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(
            settings=test_settings, gateway_config=gateway_config, upstream_client=upstream
        )
        app.state.registry.mark_health("backend-1", True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway"
        ) as client:
            response = await client.post(
                "/v1/chat/completions", headers=AUTH_HEADERS, json=chat_body()
            )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "configuration_changed"
    assert app.state.registry.snapshot()[0]["inflight"] == 0


@pytest.mark.parametrize("model", ["team/model", "openai/team/model"])
def test_model_aliases_can_contain_slashes(gateway_config, test_settings, model) -> None:
    gateway_config.models["team/model"] = gateway_config.models.pop("code-32k")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 5, "max_model_len": 1024})
        return httpx.Response(200, json={"choices": []})

    app = create_app(
        settings=test_settings, gateway_config=gateway_config,
        upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    app.state.registry.mark_health("backend-1", True)
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions", headers=AUTH_HEADERS, json=chat_body(model=model)
        )
    assert response.status_code == 200
