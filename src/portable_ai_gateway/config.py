# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

from portable_ai_gateway.schemas import CONFIG_VERSION_PATTERN


class ConfigurationError(ValueError):
    """Raised when runtime configuration is unsafe or incomplete."""


def _boolean(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean")


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < 1:
        raise ConfigurationError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be numeric") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive")
    return value


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    value = _positive_int(name, default)
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: Literal["production", "development", "test"]
    database_url: str
    cache_url: str | None
    config_path: Path
    gitops_mode: bool
    config_version: str
    source_url: str | None
    api_key_pepper: str
    oidc_issuer: str | None
    oidc_audience: str | None
    oidc_hs256_secret: str | None
    oidc_jwks_json: str | None
    provider_host_allowlist: tuple[str, ...]
    allow_anonymous_dev: bool
    max_request_bytes: int
    upstream_timeout_seconds: float
    postgres_pool_min_size: int
    postgres_pool_max_size: int
    postgres_pool_acquire_timeout_seconds: float
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        app_env = os.getenv("PAG_APP_ENV", "production").strip().lower()
        if app_env not in {"production", "development", "test"}:
            raise ConfigurationError("PAG_APP_ENV must be production, development, or test")
        source_url = os.getenv("PAG_SOURCE_URL") or None
        config_version = os.getenv("PAG_CONFIG_VERSION", "unversioned")
        issuer = os.getenv("PAG_OIDC_ISSUER") or None
        audience = os.getenv("PAG_OIDC_AUDIENCE") or None
        hs256_secret = os.getenv("PAG_OIDC_HS256_SECRET") or None
        jwks_json = os.getenv("PAG_OIDC_JWKS_JSON") or None
        pepper = os.getenv("PAG_API_KEY_PEPPER", "")
        provider_host_allowlist = tuple(
            host.strip().lower()
            for host in os.getenv("PAG_PROVIDER_HOST_ALLOWLIST", "").split(",")
            if host.strip()
        )

        if source_url:
            parsed = urlparse(source_url)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or len(source_url) > 2048
                or any(ord(character) < 32 or ord(character) == 127 for character in source_url)
            ):
                raise ConfigurationError("PAG_SOURCE_URL must be an absolute HTTPS URL")
        if re.fullmatch(CONFIG_VERSION_PATTERN, config_version) is None:
            raise ConfigurationError("PAG_CONFIG_VERSION contains unsafe characters")
        if jwks_json:
            try:
                parsed_jwks = json.loads(jwks_json)
            except json.JSONDecodeError as exc:
                raise ConfigurationError("PAG_OIDC_JWKS_JSON must contain valid JSON") from exc
            if not isinstance(parsed_jwks, dict) or not isinstance(parsed_jwks.get("keys"), list):
                raise ConfigurationError("PAG_OIDC_JWKS_JSON must be a JWKS document")
            if not all(isinstance(key, dict) for key in parsed_jwks["keys"]):
                raise ConfigurationError("PAG_OIDC_JWKS_JSON keys must be JSON objects")
            for key in parsed_jwks["keys"]:
                if key.get("kty") == "RSA" and not all(
                    isinstance(key.get(member), str) and key[member]
                    for member in ("n", "e")
                ):
                    raise ConfigurationError(
                        "PAG_OIDC_JWKS_JSON RSA keys must contain non-empty n and e values"
                    )

        allow_anonymous = _boolean("PAG_ALLOW_ANONYMOUS_DEV", False)
        if allow_anonymous and app_env == "production":
            raise ConfigurationError("PAG_ALLOW_ANONYMOUS_DEV cannot be enabled in production")
        if app_env == "production":
            missing: list[str] = []
            if not source_url:
                missing.append("PAG_SOURCE_URL")
            if len(pepper) < 32:
                missing.append("PAG_API_KEY_PEPPER (at least 32 characters)")
            if not issuer:
                missing.append("PAG_OIDC_ISSUER")
            if not audience:
                missing.append("PAG_OIDC_AUDIENCE")
            if not (hs256_secret or jwks_json):
                missing.append("PAG_OIDC_HS256_SECRET or PAG_OIDC_JWKS_JSON")
            if hs256_secret and len(hs256_secret) < 32:
                missing.append("PAG_OIDC_HS256_SECRET (at least 32 characters)")
            if jwks_json and not parsed_jwks["keys"] and not hs256_secret:
                missing.append("PAG_OIDC_JWKS_JSON (at least one key)")
            if missing:
                raise ConfigurationError("Missing production settings: " + ", ".join(missing))

        database_url = os.getenv("PAG_DATABASE_URL", "sqlite:///./portable-ai-gateway.db")
        cache_url = os.getenv("PAG_CACHE_URL") or None
        postgres_pool_min_size = _bounded_int(
            "PAG_POSTGRES_POOL_MIN_SIZE", 1, minimum=1, maximum=20
        )
        postgres_pool_max_size = _bounded_int(
            "PAG_POSTGRES_POOL_MAX_SIZE", 5, minimum=1, maximum=50
        )
        if postgres_pool_min_size > postgres_pool_max_size:
            raise ConfigurationError("PAG_POSTGRES_POOL_MIN_SIZE cannot exceed PAG_POSTGRES_POOL_MAX_SIZE")
        postgres_pool_acquire_timeout_seconds = _positive_float(
            "PAG_POSTGRES_POOL_ACQUIRE_TIMEOUT_SECONDS", 2.0
        )
        if postgres_pool_acquire_timeout_seconds > 10:
            raise ConfigurationError("PAG_POSTGRES_POOL_ACQUIRE_TIMEOUT_SECONDS must not exceed 10")
        if app_env == "production":
            if not database_url.startswith(("postgresql://", "postgres://")):
                raise ConfigurationError("Production requires PostgreSQL in PAG_DATABASE_URL")
            parsed_database = urlparse(database_url)
            database_query = parse_qs(parsed_database.query)
            socket_host = database_query.get("host", [""])[0]
            sslmode = database_query.get("sslmode", [""])[0]
            if not socket_host.startswith("/") and sslmode not in {"require", "verify-ca", "verify-full"}:
                raise ConfigurationError(
                    "Production PostgreSQL over TCP requires sslmode=require, verify-ca, or verify-full"
                )
            if not cache_url or not cache_url.startswith(("redis://", "rediss://")):
                raise ConfigurationError("Production requires a Valkey/Redis URL in PAG_CACHE_URL")

        if not pepper:
            # Fixed test/dev default is deliberately rejected in production above.
            pepper = "development-only-api-key-pepper"

        return cls(
            app_env=app_env,  # type: ignore[arg-type]
            database_url=database_url,
            cache_url=cache_url,
            config_path=Path(os.getenv("PAG_CONFIG_PATH", "/etc/portable-ai-gateway/config.yaml")),
            gitops_mode=_boolean("PAG_GITOPS_MODE", False),
            config_version=config_version,
            source_url=source_url,
            api_key_pepper=pepper,
            oidc_issuer=issuer,
            oidc_audience=audience,
            oidc_hs256_secret=hs256_secret,
            oidc_jwks_json=jwks_json,
            provider_host_allowlist=provider_host_allowlist,
            allow_anonymous_dev=allow_anonymous,
            max_request_bytes=_positive_int("PAG_MAX_REQUEST_BYTES", 1_048_576),
            upstream_timeout_seconds=_positive_float("PAG_UPSTREAM_TIMEOUT_SECONDS", 120.0),
            postgres_pool_min_size=postgres_pool_min_size,
            postgres_pool_max_size=postgres_pool_max_size,
            postgres_pool_acquire_timeout_seconds=postgres_pool_acquire_timeout_seconds,
            log_level=os.getenv("PAG_LOG_LEVEL", "INFO").upper(),
        )
