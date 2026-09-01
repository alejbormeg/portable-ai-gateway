# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

from portable_ai_gateway.providers.base import (
    Endpoint,
    PreparedStream,
    ProviderContext,
    ProviderFailure,
    ProviderResult,
)


async def _empty() -> AsyncIterator[bytes]:
    if False:  # pragma: no cover - gives the generator an async-yield shape
        yield b""


async def _chunks(values: list[bytes]) -> AsyncIterator[bytes]:
    for value in values:
        yield value


class MockProvider:
    """Deterministic, network-free provider for evaluation and conformance tests."""

    spi_version = "pag.provider/v1"

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def complete(
        self, endpoint: Endpoint, payload: dict[str, Any], context: ProviderContext
    ) -> ProviderResult:
        if self._fail:
            raise ProviderFailure("Synthetic provider failure", "mock_failure")
        fingerprint = _fingerprint(endpoint, payload)
        created = 0
        model = str(payload["model"])
        if endpoint == "responses":
            return ProviderResult(
                {
                    "id": f"resp_{fingerprint}",
                    "object": "response",
                    "created_at": created,
                    "status": "completed",
                    "model": model,
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "mock response"}],
                        }
                    ],
                    "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                }
            )
        return ProviderResult(
            {
                "id": f"chatcmpl-{fingerprint}",
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "mock response"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            }
        )

    async def prepare_stream(
        self, endpoint: Endpoint, payload: dict[str, Any], context: ProviderContext
    ) -> PreparedStream:
        if self._fail:
            raise ProviderFailure("Synthetic provider failure", "mock_failure")
        fingerprint = _fingerprint(endpoint, payload)
        if endpoint == "responses":
            response_id = f"resp_{fingerprint}"
            events = [
                {
                    "type": "response.created",
                    "sequence_number": 0,
                    "response": {
                        "id": response_id,
                        "object": "response",
                        "status": "in_progress",
                        "model": payload["model"],
                        "output": [],
                    },
                },
                {
                    "type": "response.output_text.delta",
                    "sequence_number": 1,
                    "item_id": f"msg_{fingerprint}",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "mock response",
                },
                {
                    "type": "response.completed",
                    "sequence_number": 2,
                    "response": {
                        "id": response_id,
                        "object": "response",
                        "status": "completed",
                        "model": payload["model"],
                        "output": [],
                    },
                },
            ]
            encoded = [
                f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n".encode()
                for event in events
            ]
            return PreparedStream(encoded[0], _chunks(encoded[1:]), headers={"Cache-Control": "no-cache"})
        else:
            event = {
                "id": f"chatcmpl-{fingerprint}",
                "object": "chat.completion.chunk",
                "model": payload["model"],
                "choices": [{"index": 0, "delta": {"content": "mock response"}, "finish_reason": None}],
            }
            chunk = f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode()
        return PreparedStream(chunk, _empty(), headers={"Cache-Control": "no-cache"})


def _fingerprint(endpoint: Endpoint, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"endpoint": endpoint, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()[:24]
