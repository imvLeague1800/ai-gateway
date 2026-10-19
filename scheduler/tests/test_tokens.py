from __future__ import annotations

import json

import httpx
import pytest

from app.registry import BackendRegistry
from app.tokens import TokenizationError, VllmTokenCounter


@pytest.mark.asyncio
async def test_uses_vllm_exact_tokenize_endpoint(gateway_config) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tokenize"
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"count": 321, "max_model_len": 1024, "tokens": [1, 2, 3]},
        )

    registry = BackendRegistry(gateway_config)
    registry.mark_health("backend-1", True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    counter = VllmTokenCounter(registry, client, timeout_seconds=1)

    result = await counter.count(
        "code-32k",
        {
            "model": "code-32k",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [{"type": "function", "function": {"name": "test"}}],
            "chat_template": "{{ messages[0]['content'] }}",
            "add_special_tokens": True,
            "max_tokens": 999,
            "temperature": 0.2,
        },
    )

    assert result.prompt_tokens == 321
    assert result.backend_max_model_len == 1024
    assert captured["model"] == "served-model"
    assert "tools" in captured
    assert captured["chat_template"] == "{{ messages[0]['content'] }}"
    assert captured["add_special_tokens"] is True
    assert "max_tokens" not in captured
    assert "temperature" not in captured
    await client.aclose()


@pytest.mark.asyncio
async def test_fails_closed_without_healthy_tokenizer(gateway_config) -> None:
    registry = BackendRegistry(gateway_config)
    client = httpx.AsyncClient()
    counter = VllmTokenCounter(registry, client, timeout_seconds=1)

    with pytest.raises(TokenizationError, match="no healthy tokenizer"):
        await counter.count(
            "code-32k",
            {"messages": [{"role": "user", "content": "hello"}]},
        )
    await client.aclose()
