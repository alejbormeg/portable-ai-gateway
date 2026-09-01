# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


MAX_REQUEST_BYTES = 1_048_576
app = FastAPI(
    title="Portable AI Gateway deterministic benchmark provider",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.hold_seconds = 1.5


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


async def _json_body(request: Request) -> dict[str, Any]:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            raise ValueError("request too large")
        body.extend(chunk)
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("request must be an object")
    return value


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "invalid_request_error", "code": "invalid_request"}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    try:
        payload = await _json_body(request)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return _error("invalid synthetic request")
    model = payload.get("model")
    messages = payload.get("messages")
    if not isinstance(model, str) or not model or not isinstance(messages, list) or not messages:
        return _error("model and messages are required")
    fingerprint = _fingerprint(payload)
    if payload.get("stream") is True:
        first = {
            "id": f"chatcmpl-{fingerprint}",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": "synthetic"}, "finish_reason": None}],
        }
        final = {
            "id": f"chatcmpl-{fingerprint}",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }

        async def events():
            yield f"data: {json.dumps(first, separators=(',', ':'))}\n\n".encode()
            await asyncio.sleep(app.state.hold_seconds)
            yield f"data: {json.dumps(final, separators=(',', ':'))}\n\ndata: [DONE]\n\n".encode()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )
    return JSONResponse(
        {
            "id": f"chatcmpl-{fingerprint}",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "synthetic"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def _loopback(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("host must be a literal loopback address") from exc
    if not address.is_loopback:
        raise argparse.ArgumentTypeError("benchmark provider may bind only to loopback")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the source-only deterministic HTTP provider")
    parser.add_argument("--host", type=_loopback, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--stream-hold-seconds", type=float, default=1.5)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not 0.05 <= args.stream_hold_seconds <= 30:
        parser.error("--stream-hold-seconds must be between 0.05 and 30")
    app.state.hold_seconds = args.stream_hold_seconds
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
