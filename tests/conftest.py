# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import httpx

from portable_ai_gateway.config import Settings
from portable_ai_gateway.store import InMemoryStore


@asynccontextmanager
async def asgi_client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway.test"
        ) as client:
            yield client


def b64(value: dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def jwt_token(
    secret: str,
    *,
    issuer: str = "https://issuer.example",
    audience: str | list[str] = "portable-ai-gateway",
    subject: str = "test-user",
    tenant_id: str | None = "default",
    scope: str = "admin inference:write models:read",
    expires: int | None = None,
    algorithm: str = "HS256",
) -> str:
    header = b64({"alg": algorithm, "typ": "JWT"})
    payload_data: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "scope": scope,
        "exp": expires if expires is not None else int(time.time()) + 3600,
    }
    if tenant_id is not None:
        payload_data["tenant_id"] = tenant_id
    payload = b64(payload_data)
    signing_input = f"{header}.{payload}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    return f"{header}.{payload}.{encoded_signature}"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url="memory://",
        cache_url=None,
        config_path=tmp_path / "config.yaml",
        gitops_mode=False,
        config_version="test-v1",
        source_url="https://example.test/source",
        api_key_pepper="p" * 32,
        oidc_issuer="https://issuer.example",
        oidc_audience="portable-ai-gateway",
        oidc_hs256_secret="s" * 32,
        oidc_jwks_json=None,
        provider_host_allowlist=(),
        allow_anonymous_dev=False,
        max_request_bytes=1_048_576,
        upstream_timeout_seconds=2.0,
        postgres_pool_min_size=1,
        postgres_pool_max_size=5,
        postgres_pool_acquire_timeout_seconds=2.0,
        log_level="INFO",
    )


@pytest.fixture
def store() -> InMemoryStore:
    value = InMemoryStore()
    value.initialize()
    try:
        yield value
    finally:
        value.close()
