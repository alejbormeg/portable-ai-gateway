# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json

import pytest

from portable_ai_gateway.config import ConfigurationError, Settings


def _production(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "PAG_APP_ENV": "production",
        "PAG_SOURCE_URL": "https://code.example/gateway",
        "PAG_API_KEY_PEPPER": "p" * 32,
        "PAG_OIDC_ISSUER": "https://issuer.example",
        "PAG_OIDC_AUDIENCE": "gateway",
        "PAG_OIDC_HS256_SECRET": "s" * 32,
        "PAG_OIDC_JWKS_JSON": json.dumps({"keys": []}),
        "PAG_DATABASE_URL": "postgresql://db/gateway?sslmode=require",
        "PAG_CACHE_URL": "rediss://cache/0",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_production_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAG_APP_ENV", "production")
    with pytest.raises(ConfigurationError, match="Missing production settings"):
        Settings.from_env()


def test_production_requires_postgres_and_valkey(monkeypatch: pytest.MonkeyPatch) -> None:
    _production(monkeypatch)
    monkeypatch.setenv("PAG_DATABASE_URL", "sqlite:///gateway.db")
    with pytest.raises(ConfigurationError, match="PostgreSQL"):
        Settings.from_env()
    monkeypatch.setenv("PAG_DATABASE_URL", "postgresql://db/gateway")
    with pytest.raises(ConfigurationError, match="sslmode"):
        Settings.from_env()
    monkeypatch.setenv("PAG_DATABASE_URL", "postgresql://db/gateway?sslmode=require")
    monkeypatch.delenv("PAG_CACHE_URL")
    with pytest.raises(ConfigurationError, match="Valkey"):
        Settings.from_env()


def test_production_settings_and_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    _production(monkeypatch)
    monkeypatch.setenv("PAG_PROVIDER_HOST_ALLOWLIST", "llm.internal, api.example.com")
    settings = Settings.from_env()
    assert settings.provider_host_allowlist == ("llm.internal", "api.example.com")
    assert settings.source_url == "https://code.example/gateway"


def test_anonymous_and_insecure_source_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _production(monkeypatch)
    monkeypatch.setenv("PAG_ALLOW_ANONYMOUS_DEV", "true")
    with pytest.raises(ConfigurationError, match="cannot be enabled"):
        Settings.from_env()
    monkeypatch.setenv("PAG_ALLOW_ANONYMOUS_DEV", "false")
    monkeypatch.setenv("PAG_SOURCE_URL", "http://code.example/gateway")
    with pytest.raises(ConfigurationError, match="HTTPS"):
        Settings.from_env()
    monkeypatch.setenv("PAG_SOURCE_URL", "https://code.example/gateway\nforged")
    with pytest.raises(ConfigurationError, match="HTTPS"):
        Settings.from_env()


def test_unsafe_configuration_revision_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAG_APP_ENV", "development")
    monkeypatch.setenv("PAG_CONFIG_VERSION", "revision\nforged")
    with pytest.raises(ConfigurationError, match="unsafe"):
        Settings.from_env()


def test_development_defaults_do_not_need_external_services(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAG_APP_ENV", "development")
    settings = Settings.from_env()
    assert settings.database_url.startswith("sqlite:///")
    assert settings.cache_url is None


def test_postgres_pool_settings_are_bounded_and_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAG_APP_ENV", "development")
    monkeypatch.setenv("PAG_POSTGRES_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("PAG_POSTGRES_POOL_MAX_SIZE", "5")
    monkeypatch.setenv("PAG_POSTGRES_POOL_ACQUIRE_TIMEOUT_SECONDS", "2")
    settings = Settings.from_env()
    assert settings.postgres_pool_min_size == 2
    assert settings.postgres_pool_max_size == 5
    assert settings.postgres_pool_acquire_timeout_seconds == 2

    monkeypatch.setenv("PAG_POSTGRES_POOL_MIN_SIZE", "6")
    with pytest.raises(ConfigurationError, match="cannot exceed"):
        Settings.from_env()


@pytest.mark.parametrize(
    "jwks, message",
    [
        ({"keys": [42]}, "keys must be JSON objects"),
        ({"keys": [{"kty": "RSA", "n": "value"}]}, "RSA keys must contain"),
        ({"keys": [{"kty": "RSA", "e": "AQAB"}]}, "RSA keys must contain"),
    ],
)
def test_malformed_jwks_is_rejected_at_configuration(
    monkeypatch: pytest.MonkeyPatch, jwks: dict[str, object], message: str
) -> None:
    monkeypatch.setenv("PAG_APP_ENV", "development")
    monkeypatch.setenv("PAG_OIDC_JWKS_JSON", json.dumps(jwks))
    with pytest.raises(ConfigurationError, match=message):
        Settings.from_env()
