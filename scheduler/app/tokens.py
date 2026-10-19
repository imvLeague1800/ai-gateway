from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .registry import BackendRegistry


class TokenizationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TokenCount:
    prompt_tokens: int
    backend_max_model_len: int


class VllmTokenCounter:
    """Count the rendered prompt with the serving model's exact tokenizer."""

    _CHAT_FIELDS = {
        "messages",
        "tools",
        "chat_template",
        "add_special_tokens",
        "add_generation_prompt",
        "continue_final_message",
        "chat_template_kwargs",
        "mm_processor_kwargs",
    }

    def __init__(
        self,
        registry: BackendRegistry,
        client: httpx.AsyncClient,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.registry = registry
        self.client = client
        self.timeout_seconds = timeout_seconds

    async def count(self, model_alias: str, body: dict[str, Any]) -> TokenCount:
        route = self.registry.resolve_model(model_alias)
        backend = self.registry.tokenizer_backend(model_alias)
        if route is None or backend is None:
            raise TokenizationError("no healthy tokenizer backend is available")

        payload = {
            key: value for key, value in body.items() if key in self._CHAT_FIELDS
        }
        payload["model"] = route.upstream_model
        payload["return_token_strs"] = False

        headers = {"Content-Type": "application/json"}
        api_key = self.registry.api_key_for(backend)
        if backend.api_key_env and not api_key:
            raise TokenizationError(
                f"backend credential {backend.api_key_env} is not configured"
            )
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = await self.client.post(
                f"{backend.url}/tokenize",
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            count = data["count"]
            max_model_len = data["max_model_len"]
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                or isinstance(max_model_len, bool)
                or not isinstance(max_model_len, int)
                or max_model_len <= 0
            ):
                raise ValueError("invalid token count response")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise TokenizationError(f"vLLM tokenization failed: {exc}") from exc

        return TokenCount(
            prompt_tokens=count,
            backend_max_model_len=max_model_len,
        )
