from __future__ import annotations

import httpx
import pytest

from app.config import GatewayConfig
from app.settings import Settings


@pytest.fixture
def gateway_config() -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "models": {
                "code-32k": {
                    "backend_pool": "code-default",
                    "upstream_model": "served-model",
                    "max_context_tokens": 1024,
                    "max_output_tokens": 128,
                    "default_output_tokens": 32,
                    "long_request_threshold_tokens": 64,
                }
            },
            "backend_pools": {
                "code-default": {
                    "backends": [
                        {
                            "id": "backend-1",
                            "url": "http://backend-1:8000",
                            "enabled": True,
                            "weight": 1,
                            "max_context_tokens": 1024,
                            "max_concurrent_requests": 1,
                            "max_long_requests": 1,
                        }
                    ]
                }
            },
        }
    )


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        internal_api_key="scheduler-secret",
        queue_timeout_seconds=0.1,
        max_queue_depth=10,
        max_queued_per_principal=2,
        max_active_per_principal=1,
        long_queue_promote_seconds=0.05,
        tokenize_timeout_seconds=1,
        max_request_body_bytes=4096,
        start_health_monitor=False,
    )


class AsyncChunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
