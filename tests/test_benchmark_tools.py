# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse

import httpx
import pytest

from tools.benchmark import _percentile, _validated_url
from tools.deterministic_provider import app


@pytest.mark.asyncio
async def test_source_benchmark_provider_is_deterministic_and_streams() -> None:
    app.state.hold_seconds = 0
    transport = httpx.ASGITransport(app=app)
    payload = {
        "model": "benchmark-chat",
        "messages": [{"role": "user", "content": "synthetic-benchmark"}],
        "stream": False,
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://benchmark.local") as client:
        first = await client.post("/v1/chat/completions", json=payload)
        second = await client.post("/v1/chat/completions", json=payload)
        streamed = await client.post("/v1/chat/completions", json={**payload, "stream": True})
    assert first.status_code == 200
    assert first.content == second.content
    assert streamed.status_code == 200
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert b"data: [DONE]" in streamed.content


def test_benchmark_helpers_are_fail_closed_and_use_nearest_rank() -> None:
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0
    assert (
        _validated_url("http://127.0.0.1:8080/v1/chat/completions", False)
        == "http://127.0.0.1:8080/v1/chat/completions"
    )
    with pytest.raises(argparse.ArgumentTypeError, match="non-loopback"):
        _validated_url("https://gateway.example/v1/chat/completions", False)
    with pytest.raises(argparse.ArgumentTypeError, match="credential-free"):
        _validated_url("https://user:secret@gateway.example/v1/chat/completions", True)
