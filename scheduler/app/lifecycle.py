from __future__ import annotations

import logging

import anyio
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import openai_error

logger = logging.getLogger("model_gateway.lifecycle")
TERMINATION_REASON = "gateway.termination_reason"


class RequestLifecycleMiddleware:
    """Cancel model work on disconnect or a total request deadline.

    There is exactly one reader of the server's receive channel: the application
    while uploading the body, then the disconnect watcher. The application may
    still wait for disconnect (as StreamingResponse does) using the shared event.
    No task eagerly buffers incoming request chunks.
    """

    def __init__(self, app: ASGIApp, *, timeout_seconds: float) -> None:
        self.app = app
        self.timeout_seconds = timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/v1/chat/completions"
        ):
            await self.app(scope, receive, send)
            return

        body_finished = anyio.Event()
        disconnected = anyio.Event()
        response_started = False
        response_finished = False
        streaming_response = False
        stream_at_boundary = True
        stream_tail = b""

        async with anyio.create_task_group() as tasks:
            async def app_receive() -> Message:
                if body_finished.is_set():
                    await disconnected.wait()
                    return {"type": "http.disconnect"}
                message = await receive()
                if message["type"] == "http.disconnect":
                    scope[TERMINATION_REASON] = "client_disconnected"
                    disconnected.set()
                    tasks.cancel_scope.cancel()
                    await anyio.lowlevel.checkpoint()
                if message["type"] == "http.request" and not message.get("more_body", False):
                    body_finished.set()
                return message

            async def app_send(message: Message) -> None:
                nonlocal response_started, response_finished, streaming_response
                nonlocal stream_at_boundary, stream_tail
                await send(message)
                if message["type"] == "http.response.start":
                    response_started = True
                    streaming_response = any(
                        name.lower() == b"content-type"
                        and value.lower().startswith(b"text/event-stream")
                        for name, value in message.get("headers", [])
                    )
                if streaming_response and message["type"] == "http.response.body" and message.get("body"):
                    stream_tail = (stream_tail + message["body"])[-4:]
                    stream_at_boundary = stream_tail.endswith((b"\n\n", b"\r\n\r\n", b"\r\r"))
                if message["type"] == "http.response.body" and not message.get("more_body", False):
                    response_finished = True

            async def watch_disconnect() -> None:
                await body_finished.wait()
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        disconnected.set()
                        # ASGI servers also emit disconnect after a completed
                        # response; that is normal completion, not abandonment.
                        if not response_finished:
                            scope[TERMINATION_REASON] = "client_disconnected"
                            tasks.cancel_scope.cancel()
                        return

            async def enforce_deadline() -> None:
                await anyio.sleep(self.timeout_seconds)
                if not response_finished:
                    scope[TERMINATION_REASON] = "request_timeout"
                    tasks.cancel_scope.cancel()

            tasks.start_soon(watch_disconnect)
            tasks.start_soon(enforce_deadline)
            try:
                await self.app(scope, app_receive, app_send)
            finally:
                tasks.cancel_scope.cancel()

        reason = scope.get(TERMINATION_REASON)
        if reason:
            logger.info("model request terminated reason=%s", reason)
        if reason == "request_timeout" and not disconnected.is_set() and not response_finished:
            error = openai_error(
                504,
                "The model request exceeded the gateway's total time limit.",
                error_type="server_error",
                code="request_timeout",
            )
            # Error delivery itself must not leave an abandoned socket running.
            with anyio.move_on_after(1):
                try:
                    if not response_started:
                        await error(scope, receive, send)
                    else:
                        # Headers cannot be replaced once a stream has started.
                        # Never splice an error into an incomplete upstream
                        # event. At a partial frame, close as a truncated stream.
                        # In either case, never signal successful [DONE].
                        body = (
                            b"data: " + error.body + b"\n\n"
                            if streaming_response and stream_at_boundary else b""
                        )
                        await send({"type": "http.response.body", "body": body, "more_body": False})
                except OSError:
                    pass  # The caller disconnected while the error was sent.
