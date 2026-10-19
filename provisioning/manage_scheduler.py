#!/usr/bin/env python3
"""Admin CLI for scheduler status and configuration reload."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path


def load_litellm_config(config_path: str) -> dict:
    import yaml

    with Path(config_path).open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    if not isinstance(config, dict):
        raise ValueError("LiteLLM configuration must be a YAML mapping")
    return config


def validate_route_contract(model_aliases: Iterable[str], config: dict) -> None:
    """Ensure admission receives a proxy-authenticated identity for every route."""
    settings = config.get("litellm_settings") or {}
    if not isinstance(settings, dict) or settings.get("overwrite_user_with_key_hash") is not True:
        raise ValueError("litellm_settings.overwrite_user_with_key_hash must be true")
    entries = config.get("model_list")
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("LiteLLM model_list must be an array of model mappings")
    for alias in model_aliases:
        matching = [entry for entry in entries if entry.get("model_name") == alias]
        if not matching:
            raise ValueError(f"Scheduler model alias {alias!r} is missing from LiteLLM model_list")
        for entry in matching:
            params = entry.get("litellm_params")
            if not isinstance(params, dict):
                raise ValueError(f"LiteLLM model {alias!r} requires litellm_params")
            upstream_model = params.get("model")
            if isinstance(upstream_model, str) and upstream_model.startswith("openai/"):
                allowed = params.get("allowed_openai_params")
                if not isinstance(allowed, list) or "user" not in allowed:
                    raise ValueError(
                        f"LiteLLM model {alias!r} must include user in "
                        "litellm_params.allowed_openai_params to forward the trusted identity"
                    )


def validate_timeout_alignment(request_timeout_seconds: float, config: dict) -> float:
    """Check the effective LiteLLM defaults and any explicit YAML overrides."""

    def check(value: object, label: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{label} must be a finite positive number")
        try:
            timeout = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{label} must be a finite positive number") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f"{label} must be a finite positive number")
        if timeout <= request_timeout_seconds:
            raise ValueError(f"{label} must exceed REQUEST_TIMEOUT_SECONDS ({request_timeout_seconds:g})")
        return timeout

    # Compose maps this variable to LiteLLM's native REQUEST_TIMEOUT.
    default_timeout = check(
        os.getenv("LITELLM_REQUEST_TIMEOUT_SECONDS", "1320"),
        "LITELLM_REQUEST_TIMEOUT_SECONDS",
    )
    sections = [
        ("litellm_settings", config.get("litellm_settings") or {}),
        ("router_settings", config.get("router_settings") or {}),
    ]
    for entry in config.get("model_list", []):
        sections.append((
            f"model {entry.get('model_name', '<unnamed>')}", entry.get("litellm_params") or {}
        ))
    for label, values in sections:
        for name in ("timeout", "request_timeout", "stream_timeout"):
            value = values.get(name)
            if value is None:
                continue
            if isinstance(value, str):
                raise ValueError(
                    f"{label}.{name} must be a YAML number. Prefer the inherited "
                    "LITELLM_REQUEST_TIMEOUT_SECONDS environment setting."
                )
            check(value, f"{label}.{name}")
    return default_timeout


def call(
    base_url: str, key: str, path: str, method: str = "GET", payload: dict | None = None
) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Scheduler returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach scheduler: {exc.reason}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the model scheduler")
    parser.add_argument("command", choices=["status", "reload", "validate"])
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[1] / "config" / "backends.yaml"),
        help="Backend YAML to check locally with validate (no service or key needed)",
    )
    parser.add_argument(
        "--scheduler",
        default=os.getenv("SCHEDULER_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument(
        "--litellm-config",
        default=str(Path(__file__).resolve().parents[1] / "config" / "litellm.yaml"),
        help="LiteLLM YAML whose identity forwarding and timeouts are checked by validate",
    )
    args = parser.parse_args()
    if args.command == "validate":
        # Validation uses the scheduler's schema; status/reload stay stdlib-only.
        from app.config import load_gateway_config
        from app.settings import Settings

        try:
            config = load_gateway_config(args.config)
            settings = Settings.from_env()
            litellm_config = load_litellm_config(args.litellm_config)
            validate_route_contract(config.models, litellm_config)
            litellm_timeout = validate_timeout_alignment(
                settings.request_timeout_seconds, litellm_config
            )
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        enabled = sum(
            backend.enabled
            for pool in config.backend_pools.values()
            for backend in pool.backends
        )
        print(f"Valid: {len(config.models)} model alias(es), {enabled} enabled backend(s).")
        print(
            f"Timeouts: queue {settings.queue_timeout_seconds:g}s < "
            f"scheduler total {settings.request_timeout_seconds:g}s < "
            f"LiteLLM default {litellm_timeout:g}s."
        )
        return 0
    key = os.getenv("SCHEDULER_INTERNAL_API_KEY")
    if not key:
        print("Error: SCHEDULER_INTERNAL_API_KEY is not set.", file=sys.stderr)
        return 2
    operations = {
        "status": ("/admin/backends", "GET", None),
        "reload": ("/admin/reload", "POST", {}),
    }
    path, method, payload = operations[args.command]
    try:
        result = call(args.scheduler, key, path, method, payload)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
