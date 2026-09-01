# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from typing import Any

import httpx

from portable_ai_gateway.provider_credentials import read_provider_credential
from portable_ai_gateway.providers.base import ProviderAdapter
from portable_ai_gateway.providers.mock import MockProvider
from portable_ai_gateway.providers.openai_compatible import OpenAICompatibleProvider
from portable_ai_gateway.providers.vertex import VertexProvider
from portable_ai_gateway.schemas import DEFAULT_PROVIDER_MAX_RESPONSE_BYTES


class ProviderFactory:
    """Versioned provider SPI implementation factory."""

    SPI_VERSION = "pag.provider/v1"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def create(self, provider: dict[str, Any]) -> ProviderAdapter:
        kind = provider["kind"]
        if kind == "mock":
            adapter: ProviderAdapter = MockProvider(
                provider.get("labels", {}).get("mock.fail", "false").lower() == "true"
            )
        elif kind == "openai-compatible":
            credential_env = provider.get("credential_env")
            api_key = read_provider_credential(credential_env) if credential_env else None
            adapter = OpenAICompatibleProvider(
                provider["base_url"],
                self._client,
                api_key,
                provider.get("max_response_bytes", DEFAULT_PROVIDER_MAX_RESPONSE_BYTES),
            )
        elif kind == "vertex":
            credential_env = provider.get("credential_env")
            adapter = VertexProvider(
                provider["project"],
                provider["location"],
                self._client,
                credential_env,
                provider.get("base_url"),
                provider.get("max_response_bytes", DEFAULT_PROVIDER_MAX_RESPONSE_BYTES),
            )
        else:
            raise ValueError(f"Unsupported provider kind: {kind}")
        if getattr(adapter, "spi_version", None) != self.SPI_VERSION:
            raise ValueError(f"Provider adapter requires {self.SPI_VERSION}")
        return adapter
