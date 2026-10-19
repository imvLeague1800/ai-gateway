from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


def load_key_cli():
    script = Path(__file__).resolve().parents[2] / "provisioning" / "manage_keys.py"
    spec = importlib.util.spec_from_file_location("manage_keys", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def key_cli(monkeypatch):
    module = load_key_cli()
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-admin-test")
    monkeypatch.setenv("USER_MODEL_KEY", "sk-user-test")
    return module


def test_updating_one_quota_preserves_other_key_settings(key_cli, monkeypatch, capsys):
    """Omitted limits, access, and expiry must never reset during a quota change."""
    request = Mock(return_value={"updated": True})
    monkeypatch.setattr(key_cli, "request_json", request)
    monkeypatch.setattr(
        sys, "argv",
        ["manage_keys", "update", "--key-env", "USER_MODEL_KEY", "--rpm", "12"],
    )

    assert key_cli.main() == 0
    request.assert_called_once_with(
        "http://127.0.0.1:4000", "sk-admin-test", "/key/update",
        {"key": "sk-user-test", "rpm_limit": 12},
    )
    assert json.loads(capsys.readouterr().out) == {"updated": True}


def test_update_all_supported_policy_fields(key_cli, monkeypatch):
    request = Mock(return_value={})
    monkeypatch.setattr(key_cli, "request_json", request)
    monkeypatch.setattr(
        sys, "argv",
        [
            "manage_keys", "update", "--key", "sk-user-test",
            "--models", "code-next", "review-model", "--rpm", "20",
            "--tpm", "20000", "--max-parallel", "4", "--duration", "30d",
        ],
    )

    assert key_cli.main() == 0
    assert request.call_args.args[3] == {
        "key": "sk-user-test", "models": ["code-next", "review-model"],
        "rpm_limit": 20, "tpm_limit": 20000, "max_parallel_requests": 4,
        "duration": "30d",
    }


def test_create_retains_defaults_and_principal_metadata(key_cli, monkeypatch):
    request = Mock(return_value={"key": "sk-new-test"})
    monkeypatch.setattr(key_cli, "request_json", request)
    monkeypatch.setattr(
        sys, "argv",
        ["manage_keys", "create", "--principal", "alice", "--alias", "alice-main"],
    )

    assert key_cli.main() == 0
    request.assert_called_once_with(
        "http://127.0.0.1:4000", "sk-admin-test", "/key/generate",
        {
            "key_alias": "alice-main", "user_id": "alice", "models": ["qwen3.8-27b"],
            "rpm_limit": 6, "max_parallel_requests": 3, "duration": "90d",
            "metadata": {"principal_id": "alice", "principal_type": "user"},
        },
    )


@pytest.mark.parametrize("command", ["create", "update"])
@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--rpm", "0"), ("--rpm", "-1"), ("--tpm", "0"),
        ("--tpm", "1.5"), ("--max-parallel", "-2"),
        ("--duration", "0d"), ("--duration", "-1d"),
        ("--duration", "90d-typo"),
    ],
)
def test_invalid_policy_is_rejected_before_sending(key_cli, monkeypatch, command, option, value):
    request = Mock()
    monkeypatch.setattr(key_cli, "request_json", request)
    target = (
        ["--principal", "alice", "--alias", "alice-main"]
        if command == "create" else ["--key-env", "USER_MODEL_KEY"]
    )
    monkeypatch.setattr(sys, "argv", ["manage_keys", command, *target, option, value])

    with pytest.raises(SystemExit) as exc:
        key_cli.main()
    assert exc.value.code == 2
    request.assert_not_called()


def test_update_without_changes_does_not_contact_gateway(key_cli, monkeypatch):
    request = Mock()
    monkeypatch.setattr(key_cli, "request_json", request)
    monkeypatch.setattr(
        sys, "argv", ["manage_keys", "update", "--key-env", "USER_MODEL_KEY"]
    )

    with pytest.raises(SystemExit) as exc:
        key_cli.main()
    assert exc.value.code == 2
    request.assert_not_called()


def test_missing_target_key_does_not_contact_gateway(key_cli, monkeypatch, capsys):
    request = Mock()
    monkeypatch.setattr(key_cli, "request_json", request)
    monkeypatch.delenv("USER_MODEL_KEY")
    monkeypatch.setattr(
        sys, "argv",
        ["manage_keys", "update", "--key-env", "USER_MODEL_KEY", "--tpm", "20000"],
    )

    assert key_cli.main() == 2
    request.assert_not_called()
    assert "USER_MODEL_KEY is not set" in capsys.readouterr().err


def test_update_reports_gateway_rejection(key_cli, monkeypatch, capsys):
    request = Mock(side_effect=RuntimeError("LiteLLM returned HTTP 404: key not found"))
    monkeypatch.setattr(key_cli, "request_json", request)
    monkeypatch.setattr(
        sys, "argv",
        ["manage_keys", "update", "--key-env", "USER_MODEL_KEY", "--rpm", "12"],
    )

    assert key_cli.main() == 1
    assert "key not found" in capsys.readouterr().err
