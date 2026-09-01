# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


Endpoint = Literal["chat/completions", "responses"]


@dataclass(frozen=True, slots=True)
class ProviderContext:
    request_id: str
    tenant_id: str
    provider_id: str


@dataclass(slots=True)
class ProviderResult:
    payload: dict[str, Any]
    status_code: int = 200
    headers: dict[str, str] | None = None


@dataclass(slots=True)
class PreparedStream:
    first_chunk: bytes
    remainder: AsyncIterator[bytes]
    status_code: int = 200
    headers: dict[str, str] | None = None

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        try:
            yield self.first_chunk
            async for chunk in self.remainder:
                yield chunk
        finally:
            close = getattr(self.remainder, "aclose", None)
            if close is not None:
                await close()


@dataclass(slots=True)
class ProviderFailure(Exception):
    message: str
    code: str
    retryable: bool = True
    status_code: int = 502


@runtime_checkable
class ProviderAdapter(Protocol):
    spi_version: str

    async def complete(
        self, endpoint: Endpoint, payload: dict[str, Any], context: ProviderContext
    ) -> ProviderResult: ...

    async def prepare_stream(
        self, endpoint: Endpoint, payload: dict[str, Any], context: ProviderContext
    ) -> PreparedStream: ...
