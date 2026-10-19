from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml


def litellm_routes(*aliases):
    return {
        "litellm_settings": {"overwrite_user_with_key_hash": True},
        "model_list": [
            {
                "model_name": alias,
                "litellm_params": {
                    "model": f"openai/{alias}",
                    "api_base": "http://custom-gateway.internal:9999/v1",
                    "allowed_openai_params": ["user"],
                },
            }
            for alias in (aliases or ("code-32k",))
        ],
    }


@pytest.fixture
def scheduler_cli():
    script = Path(__file__).resolve().parents[2] / "provisioning" / "manage_scheduler.py"
    spec = importlib.util.spec_from_file_location("manage_scheduler", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_timeout_alignment(scheduler_cli, monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("LITELLM_REQUEST_TIMEOUT_SECONDS", raising=False)
    config = tmp_path / "litellm.yaml"
    config.write_text("model_list: []\n", encoding="utf-8")
    assert scheduler_cli.validate_timeout_alignment(1260, scheduler_cli.load_litellm_config(str(config))) == 1320


@pytest.mark.parametrize("value", ["1200", "1260", "0", "nan", "inf", "invalid"])
def test_rejects_litellm_timeout_misalignment(
    scheduler_cli, monkeypatch, tmp_path, value
) -> None:
    monkeypatch.setenv("LITELLM_REQUEST_TIMEOUT_SECONDS", value)
    config = tmp_path / "litellm.yaml"
    config.write_text("model_list: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="LITELLM_REQUEST_TIMEOUT_SECONDS"):
        scheduler_cli.validate_timeout_alignment(1260, scheduler_cli.load_litellm_config(str(config)))


@pytest.mark.parametrize("section", ["litellm_settings", "router_settings", "model_list"])
def test_rejects_shorter_yaml_override(scheduler_cli, monkeypatch, tmp_path, section) -> None:
    monkeypatch.setenv("LITELLM_REQUEST_TIMEOUT_SECONDS", "1320")
    config = tmp_path / "litellm.yaml"
    settings = (
        {"model_list": [{"model_name": "test", "litellm_params": {"timeout": 660}}]}
        if section == "model_list" else {section: {"request_timeout": 660}}
    )
    config.write_text(yaml.safe_dump(settings), encoding="utf-8")
    with pytest.raises(ValueError, match="must exceed REQUEST_TIMEOUT_SECONDS"):
        scheduler_cli.validate_timeout_alignment(1260, scheduler_cli.load_litellm_config(str(config)))


def test_rejects_string_yaml_timeout(scheduler_cli, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LITELLM_REQUEST_TIMEOUT_SECONDS", "1320")
    config = tmp_path / "litellm.yaml"
    config.write_text(
        "litellm_settings:\n  request_timeout: os.environ/TIMEOUT\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="must be a YAML number"):
        scheduler_cli.validate_timeout_alignment(1260, scheduler_cli.load_litellm_config(str(config)))


def test_validate_uses_current_environment(
    scheduler_cli, gateway_config, monkeypatch, tmp_path, capsys
) -> None:
    backend_file = tmp_path / "backends.yaml"
    backend_file.write_text(yaml.safe_dump(gateway_config.model_dump()), encoding="utf-8")
    litellm_file = tmp_path / "litellm.yaml"
    litellm_file.write_text(yaml.safe_dump(litellm_routes()), encoding="utf-8")
    monkeypatch.setenv("QUEUE_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("LITELLM_REQUEST_TIMEOUT_SECONDS", "40")
    monkeypatch.setattr(sys, "argv", [
        "manage_scheduler", "validate", "--config", str(backend_file),
        "--litellm-config", str(litellm_file),
    ])
    assert scheduler_cli.main() == 0
    assert "queue 20s < scheduler total 30s < LiteLLM default 40s" in capsys.readouterr().out


def test_validate_reports_bad_deadline_without_contacting_service(
    scheduler_cli, gateway_config, monkeypatch, tmp_path, capsys
) -> None:
    backend_file = tmp_path / "backends.yaml"
    backend_file.write_text(yaml.safe_dump(gateway_config.model_dump()), encoding="utf-8")
    monkeypatch.setenv("QUEUE_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "100")
    monkeypatch.setattr(sys, "argv", [
        "manage_scheduler", "validate", "--config", str(backend_file)
    ])
    monkeypatch.setattr(scheduler_cli, "call", lambda *a, **k: pytest.fail("network call"))
    assert scheduler_cli.main() == 1
    assert "queue_timeout_seconds must be less" in capsys.readouterr().err


@pytest.mark.parametrize("allowed", [None, [], ["stream"], "user"])
def test_route_contract_rejects_missing_trusted_user_forwarding(scheduler_cli, allowed) -> None:
    config = litellm_routes()
    config["model_list"][0]["litellm_params"]["allowed_openai_params"] = allowed
    with pytest.raises(ValueError, match="must include user"):
        scheduler_cli.validate_route_contract(["code-32k"], config)


@pytest.mark.parametrize("overwrite", [None, False, "true", 1])
def test_route_contract_requires_server_identity_overwrite(scheduler_cli, overwrite) -> None:
    config = litellm_routes()
    config["litellm_settings"]["overwrite_user_with_key_hash"] = overwrite
    with pytest.raises(ValueError, match="overwrite_user_with_key_hash must be true"):
        scheduler_cli.validate_route_contract(["code-32k"], config)


def test_route_contract_reports_unexposed_scheduler_alias(scheduler_cli) -> None:
    with pytest.raises(ValueError, match="next-model.*missing from LiteLLM"):
        scheduler_cli.validate_route_contract(["code-32k", "next-model"], litellm_routes())


def test_route_contract_checks_every_deployment_for_an_alias(scheduler_cli) -> None:
    config = litellm_routes("code-32k", "code-32k")
    config["model_list"][1]["litellm_params"].pop("allowed_openai_params")
    with pytest.raises(ValueError, match="must include user"):
        scheduler_cli.validate_route_contract(["code-32k"], config)


def test_route_contract_allows_custom_addresses_and_unrelated_models(scheduler_cli) -> None:
    config = litellm_routes("code-32k", "team/new-model")
    config["model_list"].append({
        "model_name": "external", "litellm_params": {"model": "openai/external"}
    })
    scheduler_cli.validate_route_contract(["code-32k", "team/new-model"], config)


def test_validate_rejects_identity_regression_before_deployment(
    scheduler_cli, gateway_config, monkeypatch, tmp_path, capsys
) -> None:
    backend_file = tmp_path / "backends.yaml"
    backend_file.write_text(yaml.safe_dump(gateway_config.model_dump()), encoding="utf-8")
    litellm_file = tmp_path / "litellm.yaml"
    config = litellm_routes()
    config["model_list"][0]["litellm_params"].pop("allowed_openai_params")
    litellm_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "manage_scheduler", "validate", "--config", str(backend_file),
        "--litellm-config", str(litellm_file),
    ])
    monkeypatch.setattr(scheduler_cli, "call", lambda *a, **k: pytest.fail("network call"))
    assert scheduler_cli.main() == 1
    assert "must include user" in capsys.readouterr().err
