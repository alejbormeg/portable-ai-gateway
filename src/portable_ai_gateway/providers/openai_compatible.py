# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from portable_ai_gateway.providers.base import (
    Endpoint,
    PreparedStream,
    ProviderContext,
    ProviderFailure,
    ProviderResult,
)
from portable_ai_gateway.schemas import (
    DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
    MAX_PROVIDER_MAX_RESPONSE_BYTES,
    MIN_PROVIDER_MAX_RESPONSE_BYTES,
)


SAFE_RESPONSE_HEADERS = {"retry-after"}


@dataclass(slots=True)
class _ManagedIterator:
    iterator: AsyncIterator[bytes]
    context_manager: Any
    max_response_bytes: int
    bytes_seen: int
    closed: bool = False

    def __aiter__(self) -> "_ManagedIterator":
        return self

    async def __anext__(self) -> bytes:
        try:
            chunk = await anext(self.iterator)
        except BaseException:
            await self.aclose()
            raise
        self.bytes_seen += len(chunk)
        if self.bytes_seen > self.max_response_bytes:
            try:
                await self.aclose()
            finally:
                raise _response_too_large()
        return chunk

    async def aclose(self) -> None:
        if not self.closed:
            self.closed = True
            await self.context_manager.__aexit__(None, None, None)


class OpenAICompatibleProvider:
    spi_version = "pag.provider/v1"

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        api_key: str | None = None,
        max_response_bytes: int = DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
    ) -> None:
        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not MIN_PROVIDER_MAX_RESPONSE_BYTES
            <= max_response_bytes
            <= MAX_PROVIDER_MAX_RESPONSE_BYTES
        ):
            raise ValueError(
                "max_response_bytes must be an integer between "
                f"{MIN_PROVIDER_MAX_RESPONSE_BYTES} and {MAX_PROVIDER_MAX_RESPONSE_BYTES}"
            )
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._api_key = api_key
        self._max_response_bytes = max_response_bytes

    async def _token(self) -> str | None:
        return self._api_key

    async def complete(
        self, endpoint: Endpoint, payload: dict[str, Any], context: ProviderContext
    ) -> ProviderResult:
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/{endpoint}",
                json=payload,
                headers=await self._headers(context, False),
            ) as response:
                self._raise_for_status(response)
                body_bytes = await _read_limited(
                    response.aiter_bytes(),
                    self._max_response_bytes,
                )
                status_code = response.status_code
                headers = _safe_headers(response)
        except httpx.TimeoutException as exc:
            raise ProviderFailure("Upstream request timed out", "upstream_timeout", True, 504) from exc
        except httpx.RequestError as exc:
            raise ProviderFailure("Upstream is unavailable", "upstream_unavailable", True, 502) from exc
        try:
            body = json.loads(body_bytes)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ProviderFailure("Upstream returned invalid JSON", "invalid_upstream_response", False) from exc
        if not isinstance(body, dict):
            raise ProviderFailure("Upstream returned invalid JSON", "invalid_upstream_response", False)
        return ProviderResult(body, status_code, headers)

    async def prepare_stream(
        self, endpoint: Endpoint, payload: dict[str, Any], context: ProviderContext
    ) -> PreparedStream:
        manager = self._client.stream(
            "POST",
            f"{self._base_url}/{endpoint}",
            json=payload,
            headers=await self._headers(context, True),
        )
        try:
            response = await manager.__aenter__()
        except httpx.TimeoutException as exc:
            raise ProviderFailure("Upstream request timed out", "upstream_timeout", True, 504) from exc
        except httpx.RequestError as exc:
            raise ProviderFailure("Upstream is unavailable", "upstream_unavailable", True, 502) from exc
        try:
            self._raise_for_status(response)
            content_type = response.headers.get("content-type", "").lower()
            if not content_type.startswith("text/event-stream"):
                raise ProviderFailure("Upstream returned a non-SSE stream", "invalid_upstream_stream", False)
            iterator = response.aiter_bytes()
            first_chunk = await anext(iterator)
            if len(first_chunk) > self._max_response_bytes:
                raise _response_too_large()
        except StopAsyncIteration as exc:
            await manager.__aexit__(None, None, None)
            raise ProviderFailure("Upstream returned an empty stream", "empty_upstream_stream", True) from exc
        except httpx.RequestError as exc:
            await manager.__aexit__(None, None, None)
            raise ProviderFailure("Upstream stream is unavailable", "upstream_unavailable", True, 502) from exc
        except BaseException:
            await manager.__aexit__(None, None, None)
            raise
        remainder = _ManagedIterator(
            iterator,
            manager,
            self._max_response_bytes,
            len(first_chunk),
        )
        return PreparedStream(
            first_chunk,
            remainder,
            response.status_code,
            _safe_headers(response),
        )

    async def _headers(self, context: ProviderContext, stream: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "X-Request-ID": context.request_id,
            "User-Agent": "portable-ai-gateway/0.1.0-alpha",
        }
        token = await self._token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if not response.is_error:
            return
        retryable = response.status_code in {408, 429, 500, 502, 503, 504}
        status = response.status_code if response.status_code in {400, 401, 403, 404, 409, 429} else 502
        raise ProviderFailure("Upstream rejected the request", "upstream_error", retryable, status)


def _safe_headers(response: httpx.Response) -> dict[str, str]:
    return {key: value for key, value in response.headers.items() if key.lower() in SAFE_RESPONSE_HEADERS}


async def _read_limited(iterator: AsyncIterator[bytes], max_response_bytes: int) -> bytearray:
    body = bytearray()
    bytes_seen = 0
    async for chunk in iterator:
        bytes_seen += len(chunk)
        if bytes_seen > max_response_bytes:
            raise _response_too_large()
        body.extend(chunk)
    return body


def _response_too_large() -> ProviderFailure:
    return ProviderFailure(
        "Upstream response exceeds the configured size limit",
        "upstream_response_too_large",
        False,
        502,
    )
