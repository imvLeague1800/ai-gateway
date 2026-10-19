from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app.admission import AdmissionController
from app.config import GatewayConfig
from app.main import create_app
from app.registry import BackendRegistry


def two_gpu_config() -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "models": {
                "code-32k": {
                    "backend_pool": "fake-gpus",
                    "upstream_model": "fake-deepseek",
                    "max_context_tokens": 32768,
                    "max_output_tokens": 4096,
                    "default_output_tokens": 1024,
                    "long_request_threshold_tokens": 8192,
                }
            },
            "backend_pools": {
                "fake-gpus": {
                    "backends": [
                        {
                            "id": "fake-gpu-a",
                            "url": "http://fake-gpu-a:8000",
                            "max_context_tokens": 32768,
                            "max_concurrent_requests": 2,
                            "max_long_requests": 1,
                        },
                        {
                            "id": "fake-gpu-b",
                            "url": "http://fake-gpu-b:8000",
                            "max_context_tokens": 32768,
                            "max_concurrent_requests": 2,
                            "max_long_requests": 1,
                        },
                    ]
                }
            },
        }
    )


class FakeVllm:
    def __init__(self) -> None:
        self.generated: list[dict[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/tokenize":
            content = body["messages"][-1]["content"]
            match = re.fullmatch(r"\[tokens=(\d+)]", content)
            assert match is not None
            count = int(match.group(1))
            return httpx.Response(
                200,
                json={
                    "count": count,
                    "max_model_len": 32768,
                    "tokens": [],
                },
            )
        assert request.url.path == "/v1/chat/completions"
        self.generated.append(
            {
                "backend": request.url.host or "",
                "user": body["user"],
            }
        )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": "fake-deepseek",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )


def test_end_to_end_classifies_different_prompt_lengths(test_settings) -> None:
    config = two_gpu_config()
    fake_vllm = FakeVllm()
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(fake_vllm.handle))
    app = create_app(
        settings=test_settings,
        gateway_config=config,
        upstream_client=upstream,
    )
    app.state.registry.mark_health("fake-gpu-a", True)
    app.state.registry.mark_health("fake-gpu-b", True)

    cases = [
        ("user-short", 2_000, 4_096, 200, "interactive"),
        ("user-boundary", 8_192, 4_096, 200, "interactive"),
        ("user-long", 8_193, 4_096, 200, "long"),
        ("user-max", 28_672, 4_096, 200, "long"),
        ("user-too-large", 28_673, 4_096, 400, None),
    ]

    with TestClient(app) as client:
        for user, prompt_tokens, output_tokens, expected_status, expected_class in cases:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer scheduler-secret"},
                json={
                    "model": "code-32k",
                    "messages": [
                        {"role": "user", "content": f"[tokens={prompt_tokens}]"}
                    ],
                    "max_tokens": output_tokens,
                    "user": user,
                },
            )
            assert response.status_code == expected_status
            if expected_class is None:
                assert response.json()["error"]["code"] == "context_length_exceeded"
            else:
                assert response.headers["x-gateway-request-class"] == expected_class
                assert response.headers["x-gateway-prompt-tokens"] == str(prompt_tokens)

    assert len(fake_vllm.generated) == 4
    assert {item["user"] for item in fake_vllm.generated} == {
        "user-short",
        "user-boundary",
        "user-long",
        "user-max",
    }


@pytest.mark.asyncio
async def test_five_users_share_two_fake_gpus_fairly() -> None:
    registry = BackendRegistry(two_gpu_config())
    registry.mark_health("fake-gpu-a", True)
    registry.mark_health("fake-gpu-b", True)
    controller = AdmissionController(registry, max_active_per_principal=1)

    alice = await controller.acquire(
        "alice-1", "code-32k", "alice", "interactive", 1
    )
    bob = await controller.acquire("bob-1", "code-32k", "bob", "interactive", 1)
    carol = await controller.acquire(
        "carol-1", "code-32k", "carol", "interactive", 1
    )
    dave = await controller.acquire(
        "dave-1", "code-32k", "dave", "interactive", 1
    )
    assert {item["inflight"] for item in registry.snapshot()} == {2}

    alice_second = asyncio.create_task(
        controller.acquire("alice-2", "code-32k", "alice", "interactive", 1)
    )
    erin = asyncio.create_task(
        controller.acquire("erin-1", "code-32k", "erin", "interactive", 1)
    )
    await asyncio.sleep(0.01)
    await controller.release(carol)

    erin_reservation = await erin
    assert not alice_second.done()
    await controller.release(alice)
    await controller.release(erin_reservation)
    alice_reservation = await alice_second

    for reservation in (bob, dave, alice_reservation):
        await controller.release(reservation)
    assert all(item["inflight"] == 0 for item in registry.snapshot())


@pytest.mark.asyncio
async def test_long_requests_are_limited_without_wasting_interactive_slots() -> None:
    registry = BackendRegistry(two_gpu_config())
    registry.mark_health("fake-gpu-a", True)
    registry.mark_health("fake-gpu-b", True)
    controller = AdmissionController(registry, long_queue_promote_seconds=0)

    alice_long = await controller.acquire(
        "long-a", "code-32k", "alice", "long", 1
    )
    bob_long = await controller.acquire("long-b", "code-32k", "bob", "long", 1)
    carol_long = asyncio.create_task(
        controller.acquire("long-c", "code-32k", "carol", "long", 1)
    )
    await asyncio.sleep(0.01)

    dave_interactive = await controller.acquire(
        "normal-d", "code-32k", "dave", "interactive", 1
    )
    assert not carol_long.done()
    assert sum(item["long_inflight"] for item in registry.snapshot()) == 2
    assert sum(item["inflight"] for item in registry.snapshot()) == 3

    await controller.release(alice_long)
    carol_reservation = await carol_long
    assert carol_reservation.request_class == "long"

    for reservation in (bob_long, dave_interactive, carol_reservation):
        await controller.release(reservation)
    assert all(item["inflight"] == 0 for item in registry.snapshot())
