from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend_pool: str = Field(min_length=1)
    upstream_model: str = Field(min_length=1)
    max_context_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    default_output_tokens: int = Field(gt=0)
    long_request_threshold_tokens: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_output_limits(self) -> "ModelRoute":
        if self.default_output_tokens > self.max_output_tokens:
            raise ValueError("default_output_tokens cannot exceed max_output_tokens")
        if self.max_output_tokens >= self.max_context_tokens:
            raise ValueError("max_output_tokens must be less than max_context_tokens")
        if self.long_request_threshold_tokens >= self.max_context_tokens:
            raise ValueError(
                "long_request_threshold_tokens must be less than max_context_tokens"
            )
        return self

    def request_class(self, prompt_tokens: int) -> str:
        return (
            "long"
            if prompt_tokens > self.long_request_threshold_tokens
            else "interactive"
        )


class BackendSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    url: str | None = None
    api_key_env: str | None = None
    enabled: bool = True
    weight: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    max_context_tokens: int = Field(gt=0)
    max_concurrent_requests: int = Field(default=1, gt=0)
    max_long_requests: int = Field(default=1, ge=0)
    health_path: str = "/health"
    host_id: str | None = None

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.rstrip("/")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("backend URL is invalid") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or any(character.isspace() for character in value)
            or (port is not None and port == 0)
        ):
            raise ValueError("backend URL must be a valid http:// or https:// service URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("use api_key_env instead of credentials in the backend URL")
        if "?" in value or "#" in value:
            raise ValueError("backend URL cannot contain a query or fragment")
        if parsed.path.endswith(("/v1", "/chat/completions", "/tokenize", "/health")):
            raise ValueError("backend URL must be the service root, without /v1 or API paths")
        return value

    @field_validator("health_path")
    @classmethod
    def validate_health_path(cls, value: str) -> str:
        return value if value.startswith("/") else f"/{value}"

    @model_validator(mode="after")
    def validate_concurrency(self) -> "BackendSpec":
        if self.enabled and self.url is None:
            raise ValueError("enabled backends require a service URL")
        if self.max_long_requests > self.max_concurrent_requests:
            raise ValueError(
                "max_long_requests cannot exceed max_concurrent_requests"
            )
        return self


class BackendPool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backends: list[BackendSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_backend_ids(self) -> "BackendPool":
        ids = [backend.id for backend in self.backends]
        if len(ids) != len(set(ids)):
            raise ValueError("backend IDs must be unique within a pool")
        return self


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: dict[str, ModelRoute]
    backend_pools: dict[str, BackendPool]

    @model_validator(mode="after")
    def validate_references(self) -> "GatewayConfig":
        if not self.models:
            raise ValueError("at least one model alias is required")
        backend_ids = [
            backend.id
            for pool in self.backend_pools.values()
            for backend in pool.backends
        ]
        if len(backend_ids) != len(set(backend_ids)):
            raise ValueError("backend IDs must be unique across all pools")
        for alias, route in self.models.items():
            pool = self.backend_pools.get(route.backend_pool)
            if pool is None:
                raise ValueError(
                    f"model {alias!r} references missing pool {route.backend_pool!r}"
                )
            if not any(
                backend.max_context_tokens >= route.max_context_tokens
                for backend in pool.backends
            ):
                raise ValueError(
                    f"pool {route.backend_pool!r} has no backend capable of model {alias!r}"
                )
            if not any(
                backend.max_context_tokens >= route.max_context_tokens
                and backend.max_long_requests > 0
                for backend in pool.backends
            ):
                raise ValueError(
                    f"pool {route.backend_pool!r} cannot admit long requests for model {alias!r}"
                )
        return self


def load_gateway_config(path: str | Path) -> GatewayConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return GatewayConfig.model_validate(raw)
