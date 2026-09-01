# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import io
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import jwt_token
from portable_ai_gateway.cli import (
    apply_configuration,
    bootstrap,
    check_provider,
    doctor,
    export_configuration,
    load_configuration,
    validate_configuration,
    write_configuration,
    execute_cli,
)
from portable_ai_gateway.errors import GatewayError
from portable_ai_gateway.security import ApiKeyManager
from portable_ai_gateway.store import create_store


def _document() -> dict:
    return {
        "api_version": "gateway.portable-ai.dev/v1alpha1",
        "kind": "GatewayConfiguration",
        "metadata": {"version": "git-abc123"},
        "spec": {
            "tenants": [{"id": "acme", "name": "ACME", "sensitive": False}],
            "providers": [
                {
                    "id": "acme-mock",
                    "tenant_id": "acme",
                    "kind": "mock",
                    "residency": "local",
                    "native_responses": True,
                }
            ],
            "models": [
                {
                    "id": "acme-chat",
                    "tenant_id": "acme",
                    "upstream_model": "mock-1",
                    "capabilities": {"chat": True, "responses": True, "streaming": True},
                }
            ],
            "routes": [
                {
                    "id": "acme-route",
                    "tenant_id": "acme",
                    "model_id": "acme-chat",
                    "targets": [
                        {
                            "provider_id": "acme-mock",
                            "priority": 10,
                            "residency": "local",
                            "capabilities": {
                                "chat": True,
                                "responses": True,
                                "streaming": True,
                            },
                        }
                    ],
                }
            ],
            "policies": [],
            "quotas": [],
        },
    }


def test_config_validate_apply_and_export(tmp_path: Path, store, settings) -> None:
    source = tmp_path / "gateway.yaml"
    source.write_text(yaml.safe_dump(_document()), encoding="utf-8")
    document = load_configuration(source)
    validate_configuration(document, settings)
    stale = bootstrap(store, settings, "stale", "Stale", False, True, 7)
    assert ApiKeyManager(store, settings.api_key_pepper).authenticate(stale["api_key"]).tenant_id == "stale"
    apply_configuration(document, store, settings)
    assert store.get_resource("routes", "acme-route")["model_id"] == "acme-chat"
    assert store.get_resource("tenants", "stale") is None
    with pytest.raises(GatewayError, match="revoked"):
        ApiKeyManager(store, settings.api_key_pepper).authenticate(stale["api_key"])
    exported = export_configuration(store, "export-v1")
    target = tmp_path / "exported.yaml"
    write_configuration(exported, str(target))
    reloaded = load_configuration(target)
    assert reloaded.metadata.version == "export-v1"
    assert not hasattr(reloaded.spec, "keys")


def test_bootstrap_and_doctor_are_network_free(store, settings, monkeypatch) -> None:
    result = bootstrap(store, settings, "default", "Default", False, True, 7)
    assert result["api_key"].startswith("pag.")
    assert result["model"] == "default-mock-chat"
    expected_version = store.configuration_snapshot().config_version

    def stale_split_read(*args, **kwargs):
        del args, kwargs
        raise AssertionError("doctor must use one configuration snapshot")

    monkeypatch.setattr(store, "list_resources", stale_split_read)
    monkeypatch.setattr(store, "get_config_version", stale_split_read)
    report = doctor(store, settings)
    assert report["status"] == "ok"
    assert report["database"] == "ready"
    assert report["providers"] == 1
    assert report["config_version"] == expected_version
    assert export_configuration(store).metadata.version == expected_version


def test_sensitive_bootstrap_never_issues_a_key(store, settings) -> None:
    result = bootstrap(store, settings, "sensitive", "Sensitive", True, True, 7)
    assert "api_key" not in result


def test_bootstrap_prevalidates_key_expiry_without_partial_state(store, settings) -> None:
    with pytest.raises(Exception):
        bootstrap(store, settings, "invalid", "Invalid", False, True, -1)
    assert store.get_resource("tenants", "invalid") is None
    assert store.list_resources("providers", "invalid") == []
    assert store.list_api_keys("invalid") == []


def test_cli_bootstrap_rolls_back_state_when_audit_fails(tmp_path, settings, monkeypatch) -> None:
    configured = replace(settings, database_url=f"sqlite:///{tmp_path / 'bootstrap.db'}")
    args = SimpleNamespace(
        cli_group="bootstrap",
        tenant_id="rollback",
        tenant_name="Rollback",
        sensitive=False,
        no_api_key=False,
        api_key_expiry_days=7,
    )

    def fail_audit(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("portable_ai_gateway.cli.AuditRecorder.record", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        execute_cli(args, configured)

    persisted = create_store(configured.database_url)
    try:
        assert persisted.list_resources("tenants") == []
        assert persisted.list_resources("providers") == []
        assert persisted.list_api_keys() == []
        assert persisted.get_config_version() is None
    finally:
        persisted.close()


def test_cli_config_apply_rolls_back_reconcile_when_audit_fails(
    tmp_path, settings, monkeypatch
) -> None:
    configured = replace(settings, database_url=f"sqlite:///{tmp_path / 'apply.db'}")
    persisted = create_store(configured.database_url)
    try:
        bootstrap(persisted, configured, "existing", "Existing", False, False, 7)
        original_version = persisted.get_config_version()
    finally:
        persisted.close()

    source = tmp_path / "desired.yaml"
    source.write_text(yaml.safe_dump(_document()), encoding="utf-8")
    args = SimpleNamespace(cli_group="config", config_action="apply", file=str(source))

    def fail_audit(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("portable_ai_gateway.cli.AuditRecorder.record", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        execute_cli(args, configured)

    persisted = create_store(configured.database_url)
    try:
        assert persisted.get_resource("tenants", "existing") is not None
        assert persisted.get_resource("tenants", "acme") is None
        assert persisted.get_config_version() == original_version
    finally:
        persisted.close()


def test_cli_bootstrap_rejects_gitops_drift(settings) -> None:
    args = SimpleNamespace(
        cli_group="bootstrap",
        tenant_id="drift",
        tenant_name="Drift",
        sensitive=False,
        no_api_key=True,
        api_key_expiry_days=1,
    )
    with pytest.raises(ValueError, match="disabled in GitOps mode"):
        execute_cli(args, replace(settings, gitops_mode=True))


def test_production_cli_requires_and_audits_oidc_operator(tmp_path, settings, monkeypatch) -> None:
    configured = replace(
        settings,
        app_env="production",
        database_url=f"sqlite:///{tmp_path / 'operator.db'}",
    )
    args = SimpleNamespace(cli_group="doctor")
    monkeypatch.delenv("PAG_CLI_OIDC_TOKEN", raising=False)
    monkeypatch.delenv("PAG_CLI_OIDC_TOKEN_FILE", raising=False)
    with pytest.raises(ValueError, match="require a valid OIDC token"):
        execute_cli(args, configured, io.StringIO())

    monkeypatch.setenv(
        "PAG_CLI_OIDC_TOKEN",
        jwt_token(settings.oidc_hs256_secret, scope="admin", tenant_id=None),
    )
    assert execute_cli(args, configured, io.StringIO()) == 0
    persisted = create_store(configured.database_url)
    try:
        event = persisted.list_audit()[0]
        assert event["actor"] == "test-user"
        assert event["action"] == "system.doctor"
    finally:
        persisted.close()


def test_provider_check_resolves_the_configured_upstream_model(store, settings) -> None:
    bootstrap(store, settings, "default", "Default", False, False, 7)
    result = asyncio.run(check_provider(store, settings, "default-mock"))
    assert result["upstream_model"] == "mock-1"
    assert result["status"] == "ok"


def test_configuration_rejects_non_native_responses_route(tmp_path: Path, settings) -> None:
    raw = _document()
    raw["spec"]["providers"][0]["native_responses"] = False
    source = tmp_path / "invalid.yaml"
    source.write_text(yaml.safe_dump(raw), encoding="utf-8")
    document = load_configuration(source)
    try:
        validate_configuration(document, settings)
        assert False, "validation should reject Responses emulation"
    except Exception as exc:
        assert "native Responses" in str(exc)
