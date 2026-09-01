# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from portable_ai_gateway.provider_credentials import (
    DEFAULT_VERTEX_CREDENTIAL_ENV,
    read_provider_credential,
    validate_provider_credential_env,
)
from portable_ai_gateway.providers.base import ProviderFailure
from portable_ai_gateway.providers.openai_compatible import OpenAICompatibleProvider
from portable_ai_gateway.provider_endpoints import vertex_base_url
from portable_ai_gateway.schemas import DEFAULT_PROVIDER_MAX_RESPONSE_BYTES


class VertexTokenProvider:
    """Uses an injected environment token or Google ADC without embedding credentials."""

    def __init__(self, credential_env: str | None = None) -> None:
        self._credential_env = validate_provider_credential_env(
            credential_env or DEFAULT_VERTEX_CREDENTIAL_ENV
        )
        self._credentials: Any = None

    async def token(self) -> str:
        injected = read_provider_credential(self._credential_env)
        if injected:
            return injected
        try:
            import google.auth
            from google.auth.transport.requests import Request
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install portable-ai-gateway[vertex] to use Google ADC") from exc
        if self._credentials is None:
            self._credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
        # google-auth owns expiry representation (which has historically been a
        # naive UTC datetime); its validity property avoids mixing datetime forms.
        if not self._credentials.valid:
            await asyncio.to_thread(self._credentials.refresh, Request())
        return str(self._credentials.token)


class VertexProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        project: str,
        location: str,
        client: httpx.AsyncClient,
        credential_env: str | None = None,
        base_url: str | None = None,
        max_response_bytes: int = DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
    ) -> None:
        resolved_url = base_url or vertex_base_url(project, location)
        super().__init__(resolved_url, client, max_response_bytes=max_response_bytes)
        self._tokens = VertexTokenProvider(credential_env)

    async def _token(self) -> str:
        try:
            return await self._tokens.token()
        except Exception as exc:
            raise ProviderFailure(
                "Vertex credentials are unavailable",
                "vertex_authentication_unavailable",
                True,
                502,
            ) from exc
