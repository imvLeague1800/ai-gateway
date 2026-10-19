from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from app.main import create_app
from app.lifecycle import RequestLifecycleMiddleware


def scope_for(spec_version="2.4"):
    return {
        "type": "http", "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/v1/chat/completions", "raw_path": b"/v1/chat/completions",
        "query_string": b"", "headers": [(b"authorization", b"Bearer scheduler-secret")],
        "server": ("gateway", 80), "client": ("test", 123), "root_path": "",
    }


class Connection:
    def __init__(self, *, streaming=False):
        self.body = json.dumps({
            "model": "code-32k", "user": "test-key", "max_tokens": 16,
            "messages": [{"role": "user", "content": "hello"}], "stream": streaming,
        }).encode()
        self.disconnected = asyncio.Event()
        self.received = False
        self.sent = []
        self.reading = False

    async def receive(self):
        assert not self.reading, "two tasks consumed the ASGI receive channel"
        self.reading = True
        try:
            if not self.received:
                self.received = True
                return {"type": "http.request", "body": self.body, "more_body": False}
            await self.disconnected.wait()
            return {"type": "http.disconnect"}
        finally:
            self.reading = False

    async def send(self, message):
        self.sent.append(message)


class PausingBackend:
    def __init__(self, stage):
        self.stage = stage
        self.reached = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.closed = asyncio.Event()
        self.generations = 0

    async def pause(self):
        self.reached.set()
        try:
            await asyncio.Future()
        finally:
            self.cancelled.set()

    async def handle(self, request):
        if request.url.path == "/tokenize":
            if self.stage == "tokenize":
                await self.pause()
            return httpx.Response(200, json={"count": 5, "max_model_len": 1024})
        self.generations += 1
        if self.stage == "headers":
            await self.pause()
        backend = self

        class PausingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"choices":[]}\n\n'
                await backend.pause()

            async def aclose(self):
                await asyncio.sleep(0)
                backend.closed.set()

        if self.stage in {"nonstream_body", "stream_body"}:
            return httpx.Response(200, stream=PausingStream(), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": []})


async def wait_queued(app):
    async def waiting():
        while not app.state.controller.queue_depths():
            await asyncio.sleep(0)
    await asyncio.wait_for(waiting(), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
@pytest.mark.parametrize("stage", ["tokenize", "queued", "headers", "nonstream_body", "stream_body"])
async def test_disconnect_cancels_work_at_every_stage(gateway_config, test_settings, stage, spec_version):
    backend = PausingBackend(stage)
    connection = Connection(streaming=stage == "stream_body")
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend.handle)) as upstream:
        app = create_app(settings=test_settings, gateway_config=gateway_config, upstream_client=upstream)
        app.state.registry.mark_health("backend-1", True)
        blocker = None
        if stage == "queued":
            blocker = await app.state.controller.acquire("blocker", "code-32k", "another-key", "interactive", 1)
        task = asyncio.create_task(app(scope_for(spec_version), connection.receive, connection.send))
        if stage == "queued":
            await wait_queued(app)
        else:
            await asyncio.wait_for(backend.reached.wait(), 1)
        connection.disconnected.set()
        await asyncio.wait_for(task, 1)
        assert app.state.controller.queue_depths() == {}
        if blocker:
            assert backend.generations == 0
            await app.state.controller.release(blocker)
        else:
            assert backend.cancelled.is_set()
        assert app.state.registry.snapshot()[0]["inflight"] == 0
        assert not app.state.controller._active_by_principal
        if stage in {"nonstream_body", "stream_body"}:
            assert backend.closed.is_set()
        # The same key can use the recovered capacity immediately.
        backend.stage = "healthy"
        fresh = Connection()
        await asyncio.wait_for(app(scope_for(spec_version), fresh.receive, fresh.send), 1)
        assert fresh.sent[0]["status"] == 200
        assert app.state.registry.snapshot()[0]["inflight"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["tokenize", "headers", "nonstream_body", "stream_body"])
async def test_total_deadline_cancels_work_and_reports_failure(gateway_config, test_settings, stage):
    backend = PausingBackend(stage)
    connection = Connection(streaming=stage == "stream_body")
    settings = replace(test_settings, request_timeout_seconds=0.04, queue_timeout_seconds=0.01)
    async with httpx.AsyncClient(transport=httpx.MockTransport(backend.handle)) as upstream:
        app = create_app(settings=settings, gateway_config=gateway_config, upstream_client=upstream)
        app.state.registry.mark_health("backend-1", True)
        await asyncio.wait_for(app(scope_for(), connection.receive, connection.send), 1)
    assert backend.cancelled.is_set()
    assert app.state.registry.snapshot()[0]["inflight"] == 0
    assert not app.state.controller._active_by_principal
    assert connection.sent[0]["status"] == (200 if stage == "stream_body" else 504)
    body = b"".join(message.get("body", b"") for message in connection.sent)
    assert b'"code":"request_timeout"' in body
    assert b"[DONE]" not in body
    if stage == "stream_body":
        assert connection.sent[-1]["more_body"] is False


@pytest.mark.asyncio
async def test_upload_deadline_and_disconnect_leave_no_tasks(gateway_config, test_settings):
    settings = replace(test_settings, request_timeout_seconds=0.02, queue_timeout_seconds=0.01)
    app = create_app(settings=settings, gateway_config=gateway_config)
    connection = Connection()

    async def never_finishes_upload():
        if not connection.received:
            connection.received = True
            return {"type": "http.request", "body": b'{"model":', "more_body": True}
        await asyncio.Future()

    await asyncio.wait_for(app(scope_for(), never_finishes_upload, connection.send), 1)
    assert connection.sent[0]["status"] == 504
    assert app.state.controller.queue_depths() == {}

    async def disconnected_upload():
        return {"type": "http.disconnect"}

    connection.sent.clear()
    await asyncio.wait_for(app(scope_for(), disconnected_upload, connection.send), 1)
    assert connection.sent == []


@pytest.mark.asyncio
async def test_healthy_chunked_upload_is_consumed_once(gateway_config, test_settings):
    backend = PausingBackend("healthy")
    connection = Connection()
    parts = iter([connection.body[:10], connection.body[10:]])
    received = 0

    async def receive():
        nonlocal received
        if received < 2:
            received += 1
            await asyncio.sleep(0)
            return {"type": "http.request", "body": next(parts), "more_body": received < 2}
        await connection.disconnected.wait()
        return {"type": "http.disconnect"}

    async with httpx.AsyncClient(transport=httpx.MockTransport(backend.handle)) as upstream:
        app = create_app(settings=test_settings, gateway_config=gateway_config, upstream_client=upstream)
        app.state.registry.mark_health("backend-1", True)
        await asyncio.wait_for(app(scope_for(), receive, connection.send), 1)
    assert received == 2
    assert connection.sent[0]["status"] == 200
    assert backend.generations == 1


@pytest.mark.asyncio
async def test_deadline_stops_a_stream_that_keeps_sending_data(gateway_config, test_settings):
    closed = asyncio.Event()

    class BusyStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b'data: {"choices":[]}\n\n'
                await asyncio.sleep(0.002)

        async def aclose(self):
            closed.set()

    def handler(request):
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 5, "max_model_len": 1024})
        return httpx.Response(200, stream=BusyStream(), headers={"content-type": "text/event-stream"})

    settings = replace(test_settings, request_timeout_seconds=0.03, queue_timeout_seconds=0.01)
    connection = Connection(streaming=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(settings=settings, gateway_config=gateway_config, upstream_client=upstream)
        app.state.registry.mark_health("backend-1", True)
        await asyncio.wait_for(app(scope_for(), connection.receive, connection.send), 1)
    assert closed.is_set()
    assert app.state.registry.snapshot()[0]["inflight"] == 0
    assert b'"code":"request_timeout"' in connection.sent[-1]["body"]


@pytest.mark.asyncio
async def test_stalled_close_does_not_prevent_releasing_capacity(gateway_config, test_settings):
    closing = asyncio.Event()

    class StalledClose(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"{}"

        async def aclose(self):
            closing.set()
            await asyncio.Future()

    def handler(request):
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"count": 5, "max_model_len": 1024})
        return httpx.Response(200, stream=StalledClose())

    settings = replace(test_settings, request_timeout_seconds=0.04, queue_timeout_seconds=0.01,
                       upstream_connect_timeout_seconds=0.01)
    connection = Connection()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(settings=settings, gateway_config=gateway_config, upstream_client=upstream)
        app.state.registry.mark_health("backend-1", True)
        await asyncio.wait_for(app(scope_for(), connection.receive, connection.send), 1)
    assert closing.is_set()
    assert app.state.registry.snapshot()[0]["inflight"] == 0


@pytest.mark.asyncio
async def test_timeout_while_headers_are_blocked_sends_a_valid_error_response():
    delivered = []
    first = True

    async def response_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def backpressured_send(message):
        nonlocal first
        if first:
            first = False
            await asyncio.Future()
        delivered.append(message)

    connection = Connection()
    middleware = RequestLifecycleMiddleware(response_app, timeout_seconds=0.02)
    await asyncio.wait_for(middleware(scope_for(), connection.receive, backpressured_send), 1)
    assert delivered[0]["type"] == "http.response.start"
    assert delivered[0]["status"] == 504
    assert delivered[1]["type"] == "http.response.body"


@pytest.mark.asyncio
@pytest.mark.parametrize("chunks,can_append_error", [
    ([b'data: {"choices":['], False),
    ([b'data: {}\n', b'\n'], True),
    ([b'data: {}\r\n\r', b'\n'], True),
])
async def test_stream_timeout_never_splices_an_error_into_a_partial_event(chunks, can_append_error):
    connection = Connection()

    async def response_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        for chunk in chunks:
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await asyncio.Future()

    middleware = RequestLifecycleMiddleware(response_app, timeout_seconds=0.02)
    await asyncio.wait_for(middleware(scope_for(), connection.receive, connection.send), 1)
    final = connection.sent[-1]
    assert final["more_body"] is False
    if can_append_error:
        assert final["body"].startswith(b'data: {"error":')
    else:
        assert final["body"] == b""
