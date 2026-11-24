from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import BackendSpec, GatewayConfig, load_gateway_config
from pathlib import Path


def test_rejects_model_without_capable_backend() -> None:
    with pytest.raises(ValidationError, match="no backend capable"):
        GatewayConfig.model_validate(
            {
                "models": {
                    "large": {
                        "backend_pool": "pool",
                        "upstream_model": "served",
                        "max_context_tokens": 200,
                        "max_output_tokens": 20,
                        "default_output_tokens": 10,
                        "long_request_threshold_tokens": 100,
                    }
                },
                "backend_pools": {
                    "pool": {
                        "backends": [
                            {
                                "id": "small",
                                "url": "http://small:8000/",
                                "max_context_tokens": 100,
                            }
                        ]
                    }
                },
            }
        )


def test_normalizes_backend_url(gateway_config: GatewayConfig) -> None:
    backend = gateway_config.backend_pools["code-default"].backends[0]
    assert backend.url == "http://backend-1:8000"


def test_backend_ids_must_be_unique_across_pools() -> None:
    with pytest.raises(ValidationError, match="unique across all pools"):
        GatewayConfig.model_validate(
            {
                "models": {
                    "model-a": {
                        "backend_pool": "pool-a",
                        "upstream_model": "served",
                        "max_context_tokens": 100,
                        "max_output_tokens": 10,
                        "default_output_tokens": 5,
                        "long_request_threshold_tokens": 50,
                    }
                },
                "backend_pools": {
                    "pool-a": {
                        "backends": [
                            {"id": "duplicate", "url": "http://a", "max_context_tokens": 100}
                        ]
                    },
                    "pool-b": {
                        "backends": [
                            {"id": "duplicate", "url": "http://b", "max_context_tokens": 100}
                        ]
                    },
                },
            }
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://",
        "http://host:wrong",
        "http://host:0",
        "http://host:8000/v1",
        "http://host:8000/v1/chat/completions",
        "http://host:8000/tokenize",
        "http://user:secret@host:8000",
        "http://host:8000?key=secret",
        "http://host:8000#fragment",
        "http://host:8000?",
        "http://host:8000#",
        "http://a host:8000",
    ],
)
def test_rejects_invalid_service_root(url: str) -> None:
    with pytest.raises(ValidationError):
        BackendSpec(id="backend", url=url, max_context_tokens=32768)


def test_pending_host_can_have_no_endpoint() -> None:
    backend = BackendSpec(
        id="pending", host_id="10.169.25.16", enabled=False, max_context_tokens=32768
    )
    assert backend.url is None
    with pytest.raises(ValidationError, match="enabled backends require a service URL"):
        BackendSpec.model_validate({**backend.model_dump(), "enabled": True})


@pytest.mark.parametrize("weight", [float("inf"), float("nan")])
def test_rejects_non_finite_backend_weight(weight: float) -> None:
    with pytest.raises(ValidationError):
        BackendSpec(id="backend", url="http://host", max_context_tokens=32768, weight=weight)


def test_shipped_spark_configuration() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_gateway_config(root / "config" / "backends.yaml")
    route = config.models["qwen3.8-27b"]
    assert route.upstream_model == "qwen3.6-35b-a3b-nvfp4"
    assert route.max_context_tokens == 262144
    first, second = config.backend_pools[route.backend_pool].backends
    assert first.max_concurrent_requests == 2 and second.max_concurrent_requests == 2
    assert first.max_long_requests == 2 and second.max_long_requests == 2
    assert first.host_id == "10.169.25.15"
    assert first.url == "http://10.169.25.15:8000"
    assert first.enabled
    assert second.host_id == "10.169.25.16"
    assert second.url == "http://10.169.25.16:8000"
    assert second.enabled
    local = load_gateway_config(root / "config" / "backends.local.yaml")
    assert local.backend_pools["local-vllm"].backends[0].url == "http://vllm:8000"
