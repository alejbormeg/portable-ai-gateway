# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import datetime

import httpx
import pytest
from pydantic import ValidationError

from portable_ai_gateway.providers.factory import ProviderFactory
from portable_ai_gateway.providers.base import ProviderContext, ProviderFailure
from portable_ai_gateway.providers.openai_compatible import OpenAICompatibleProvider
from portable_ai_gateway.providers.mock import MockProvider
from portable_ai_gateway.providers.vertex import VertexProvider
from portable_ai_gateway.schemas import (
    DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
    MAX_PROVIDER_MAX_RESPONSE_BYTES,
    MIN_PROVIDER_MAX_RESPONSE_BYTES,
    Provider,
)


@pytest.mark.asyncio
async def test_openai_compatible_forwards_auth_model_and_request_id() -> None:
    async def upstream(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://llm.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer external-secret"
        assert request.headers["x-request-id"] == "request-1"
        assert b'"model":"upstream"' in request.content
        return httpx.Response(
            200,
            headers={"Cache-Control": "public, max-age=3600", "Retry-After": "3"},
            json={"id": "ok", "object": "chat.completion"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        provider = OpenAICompatibleProvider("https://llm.example/v1", client, "external-secret")
        result = await provider.complete(
            "chat/completions",
            {"model": "upstream", "messages": [{"role": "user", "content": "hi"}]},
            ProviderContext("request-1", "default", "external"),
        )
    assert result.payload["id"] == "ok"
    assert result.headers == {"retry-after": "3"}


@pytest.mark.asyncio
async def test_openai_compatible_prefetches_first_stream_chunk() -> None:
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: first\n\n"
            yield b"data: [DONE]\n\n"

    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Stream(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        provider = OpenAICompatibleProvider("https://llm.example/v1", client)
        prepared = await provider.prepare_stream(
            "chat/completions",
            {"model": "upstream", "stream": True},
            ProviderContext("request-1", "default", "external"),
        )
        chunks = [chunk async for chunk in prepared.iter_bytes()]
    assert chunks[0].startswith(b"data: first")


@pytest.mark.asyncio
async def test_oversized_non_streaming_response_is_non_retryable_and_closed() -> None:
    closed = False

    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"content":"'
            yield b"x" * MIN_PROVIDER_MAX_RESPONSE_BYTES
            yield b'"}'

        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=OversizedStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        provider = OpenAICompatibleProvider(
            "https://llm.example/v1",
            client,
            max_response_bytes=MIN_PROVIDER_MAX_RESPONSE_BYTES,
        )
        with pytest.raises(ProviderFailure) as raised:
            await provider.complete(
                "chat/completions",
                {"model": "upstream"},
                ProviderContext("request-1", "default", "external"),
            )
        assert closed is True
    assert raised.value.code == "upstream_response_too_large"
    assert raised.value.retryable is False
    assert raised.value.status_code == 502


@pytest.mark.asyncio
async def test_oversized_first_stream_chunk_is_non_retryable_and_closed() -> None:
    closed = False

    class OversizedFirstChunk(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: " + (b"x" * MIN_PROVIDER_MAX_RESPONSE_BYTES) + b"\n\n"

        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=OversizedFirstChunk(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        provider = OpenAICompatibleProvider(
            "https://llm.example/v1",
            client,
            max_response_bytes=MIN_PROVIDER_MAX_RESPONSE_BYTES,
        )
        with pytest.raises(ProviderFailure) as raised:
            await provider.prepare_stream(
                "chat/completions",
                {"model": "upstream", "stream": True},
                ProviderContext("request-1", "default", "external"),
            )
        assert closed is True
    assert raised.value.code == "upstream_response_too_large"
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_stream_accumulated_limit_closes_after_first_byte_without_fallback_signal() -> None:
    closed = False
    first = b"data: " + (b"a" * 500) + b"\n\n"
    second = b"data: " + (b"b" * 600) + b"\n\n"

    class OversizedRemainder(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield first
            yield second

        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=OversizedRemainder(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        provider = OpenAICompatibleProvider(
            "https://llm.example/v1",
            client,
            max_response_bytes=MIN_PROVIDER_MAX_RESPONSE_BYTES,
        )
        prepared = await provider.prepare_stream(
            "chat/completions",
            {"model": "upstream", "stream": True},
            ProviderContext("request-1", "default", "external"),
        )
        exposed: list[bytes] = []
        with pytest.raises(ProviderFailure) as raised:
            async for chunk in prepared.iter_bytes():
                exposed.append(chunk)
        assert closed is True
    assert exposed == [first]
    assert raised.value.code == "upstream_response_too_large"
    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_request_errors_before_first_stream_byte_are_retryable_and_closed() -> None:
    closed = False

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise httpx.RemoteProtocolError("upstream disconnected")
            yield b""  # pragma: no cover - async generator shape

        async def aclose(self) -> None:
            nonlocal closed
            closed = True

    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=BrokenStream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        provider = OpenAICompatibleProvider("https://llm.example/v1", client)
        with pytest.raises(ProviderFailure) as raised:
            await provider.prepare_stream(
                "chat/completions",
                {"model": "upstream", "stream": True},
                ProviderContext("request-1", "default", "external"),
            )
    assert raised.value.retryable is True
    assert raised.value.code == "upstream_unavailable"
    assert closed is True


@pytest.mark.asyncio
async def test_non_retryable_upstream_error_stops_fallback_contract() -> None:
    def upstream(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        provider = OpenAICompatibleProvider("https://llm.example/v1", client)
        with pytest.raises(ProviderFailure) as raised:
            await provider.complete(
                "chat/completions",
                {"model": "upstream"},
                ProviderContext("request-1", "default", "external"),
            )
    assert raised.value.retryable is False
    assert raised.value.status_code == 400


def test_vertex_url_is_derived_without_customer_identifiers() -> None:
    client = httpx.AsyncClient()
    try:
        provider = VertexProvider("customer-project", "europe-west1", client)
        assert "customer-project" in provider._base_url
        assert "europe-west1" in provider._base_url
        assert "/v1/projects/" in provider._base_url
        assert "/v1beta1/" not in provider._base_url
    finally:
        import asyncio
        asyncio.run(client.aclose())


def test_provider_response_limit_is_bounded_and_reaches_factory_adapters() -> None:
    provider = Provider(
        id="external",
        tenant_id="default",
        kind="openai-compatible",
        base_url="https://llm.example/v1",
    )
    assert provider.max_response_bytes == DEFAULT_PROVIDER_MAX_RESPONSE_BYTES
    for invalid in (MIN_PROVIDER_MAX_RESPONSE_BYTES - 1, MAX_PROVIDER_MAX_RESPONSE_BYTES + 1):
        with pytest.raises(ValidationError, match="max_response_bytes"):
            Provider(
                id="external",
                tenant_id="default",
                kind="openai-compatible",
                base_url="https://llm.example/v1",
                max_response_bytes=invalid,
            )

    client = httpx.AsyncClient()
    try:
        openai_adapter = ProviderFactory(client).create(
            {
                "kind": "openai-compatible",
                "base_url": "https://llm.example/v1",
                "max_response_bytes": MIN_PROVIDER_MAX_RESPONSE_BYTES,
            }
        )
        vertex_adapter = ProviderFactory(client).create(
            {
                "kind": "vertex",
                "project": "customer-project",
                "location": "europe-west1",
                "max_response_bytes": MIN_PROVIDER_MAX_RESPONSE_BYTES,
            }
        )
        assert openai_adapter._max_response_bytes == MIN_PROVIDER_MAX_RESPONSE_BYTES
        assert vertex_adapter._max_response_bytes == MIN_PROVIDER_MAX_RESPONSE_BYTES
    finally:
        import asyncio

        asyncio.run(client.aclose())


def test_provider_cannot_select_an_unrelated_gateway_environment_secret(monkeypatch) -> None:
    monkeypatch.setenv("PAG_API_KEY_PEPPER", "must-never-be-forwarded")
    with pytest.raises(ValidationError, match="PAG_PROVIDER_CREDENTIAL"):
        Provider(
            id="malicious",
            tenant_id="default",
            kind="openai-compatible",
            base_url="https://llm.example/v1",
            credential_env="PAG_API_KEY_PEPPER",
        )

    client = httpx.AsyncClient()
    try:
        with pytest.raises(ValueError, match="PAG_PROVIDER_CREDENTIAL"):
            ProviderFactory(client).create(
                {
                    "id": "unvalidated",
                    "kind": "openai-compatible",
                    "base_url": "https://llm.example/v1",
                    "credential_env": "PAG_OIDC_HS256_SECRET",
                }
            )
    finally:
        import asyncio
        asyncio.run(client.aclose())


@pytest.mark.asyncio
async def test_mock_output_and_provider_spi_are_deterministic() -> None:
    provider = MockProvider()
    payload = {"model": "mock-1", "messages": [{"role": "user", "content": "synthetic"}]}
    first = await provider.complete(
        "chat/completions", payload, ProviderContext("request-one", "default", "mock")
    )
    second = await provider.complete(
        "chat/completions", payload, ProviderContext("request-two", "default", "mock")
    )
    assert provider.spi_version == "pag.provider/v1"
    assert first.payload == second.payload


@pytest.mark.asyncio
async def test_vertex_uses_google_auth_validity_without_comparing_naive_expiry(monkeypatch) -> None:
    class Credentials:
        token = "old"
        expiry = datetime.now()
        valid = False

        def refresh(self, _request) -> None:
            self.token = "fresh"
            self.valid = True

    monkeypatch.delenv("PAG_PROVIDER_CREDENTIAL_VERTEX_ACCESS_TOKEN", raising=False)

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr("portable_ai_gateway.providers.vertex.asyncio.to_thread", immediate)
    async with httpx.AsyncClient() as client:
        provider = VertexProvider("customer-project", "europe-west1", client)
        provider._tokens._credentials = Credentials()
        assert await provider._token() == "fresh"
