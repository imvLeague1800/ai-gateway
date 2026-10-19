#!/usr/bin/env python3
"""Admin CLI for LiteLLM virtual keys. Uses only the Python standard library."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def request_json(
    base_url: str,
    master_key: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    method: str = "POST",
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={
            "Authorization": f"Bearer {master_key}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LiteLLM returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach LiteLLM: {exc.reason}") from exc


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--gateway",
        default=os.getenv("LITELLM_GATEWAY_URL", "http://127.0.0.1:4000"),
        help="LiteLLM base URL",
    )
    parser.add_argument(
        "--master-key-env",
        default="LITELLM_MASTER_KEY",
        help="Environment variable containing the LiteLLM master key",
    )


def positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def positive_duration(value: str) -> str:
    aliases = {"hourly", "daily", "weekly", "monthly"}
    match = re.fullmatch(r"(\d+)(mo|[smhdw])", value)
    if value not in aliases and (match is None or int(match[1]) < 1):
        raise argparse.ArgumentTypeError(
            "must be a positive duration, such as 30m, 24h, 90d, or 1mo"
        )
    return value


def add_key_target(parser: argparse.ArgumentParser) -> None:
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--key", help="Target virtual key")
    target.add_argument(
        "--key-env",
        help="Environment variable containing the target key (safer for shell history)",
    )


def add_policy(parser: argparse.ArgumentParser, *, creating: bool) -> None:
    parser.add_argument(
        "--models",
        nargs="+",
        default=["qwen3.8-27b"] if creating else None,
        help="Allowed model aliases",
    )
    parser.add_argument(
        "--rpm", type=positive_integer, default=6 if creating else None,
        help="Requests per minute",
    )
    parser.add_argument(
        "--tpm", type=positive_integer, default=None,
        help=(
            "Tokens per minute; omit until calibrated"
            if creating else "Tokens per minute; omit to keep the current limit"
        ),
    )
    parser.add_argument(
        "--max-parallel",
        type=positive_integer,
        default=3 if creating else None,
        help="Maximum outstanding requests (1 active plus 2 queued by default)",
    )
    parser.add_argument(
        "--duration", type=positive_duration, default="90d" if creating else None,
        help="Key lifetime from now, such as 90d; omit on update to keep expiry",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage model-gateway API keys")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create a user or service key")
    add_common(create)
    create.add_argument("--principal", required=True, help="Canonical user/service ID")
    create.add_argument("--alias", required=True, help="Human-readable key alias")
    add_policy(create, creating=True)
    create.add_argument(
        "--principal-type",
        choices=["user", "service"],
        default="user",
    )
    create.add_argument("--team", default=None)

    update = subparsers.add_parser("update", help="Change limits on an existing key")
    add_common(update)
    add_key_target(update)
    add_policy(update, creating=False)

    for command in ("block", "unblock", "info"):
        item = subparsers.add_parser(command, help=f"{command.title()} an existing key")
        add_common(item)
        add_key_target(item)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    changes = {}
    if args.command == "update":
        fields = {
            "models": "models",
            "rpm": "rpm_limit",
            "tpm": "tpm_limit",
            "max_parallel": "max_parallel_requests",
            "duration": "duration",
        }
        changes = {
            field: getattr(args, argument)
            for argument, field in fields.items()
            if getattr(args, argument) is not None
        }
        if not changes:
            parser.error("update requires at least one model, quota, or duration option")
    master_key = os.getenv(args.master_key_env)
    if not master_key:
        print(
            f"Error: environment variable {args.master_key_env} is not set.",
            file=sys.stderr,
        )
        return 2

    if args.command == "create":
        metadata = {
            "principal_id": args.principal,
            "principal_type": args.principal_type,
        }
        if args.team:
            metadata["team"] = args.team
        payload: dict[str, Any] = {
            "key_alias": args.alias,
            "user_id": args.principal,
            "models": args.models,
            "rpm_limit": args.rpm,
            "max_parallel_requests": args.max_parallel,
            "duration": args.duration,
            "metadata": metadata,
        }
        if args.tpm is not None:
            payload["tpm_limit"] = args.tpm
        path = "/key/generate"
    else:
        target_key = args.key or os.getenv(args.key_env)
        if not target_key:
            print(
                f"Error: environment variable {args.key_env} is not set.",
                file=sys.stderr,
            )
            return 2
        path = {
            "update": "/key/update",
            "block": "/key/block",
            "unblock": "/key/unblock",
            "info": "/key/info",
        }[args.command]
        payload = {"key": target_key, **changes}

    try:
        if args.command == "info":
            result = request_json(
                args.gateway,
                master_key,
                path,
                method="GET",
                query={"key": target_key},
            )
        else:
            result = request_json(args.gateway, master_key, path, payload)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
