# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import time

import pytest

from conftest import asgi_client
from portable_ai_gateway.apps import create_data_plane
from portable_ai_gateway.cli import bootstrap
from portable_ai_gateway.errors import GatewayError
from portable_ai_gateway.providers.base import ProviderFailure
from portable_ai_gateway.routing import GatewayRouter
from portable_ai_gateway.schemas import Capabilities, Model, Provider, Route, RouteTarget


CAPABILITY_REQUESTS = (
    (
        "tools-field",
        "/v1/chat/completions",
        {"messages": [{"role": "user", "content": "synthetic"}], "tools": []},
        "tools",
    ),
    (
        "tool-choice",
        "/v1/chat/completions",
        {"messages": [{"role": "user", "content": "synthetic"}], "tool_choice": "none"},
        "tools",
    ),
    (
        "tool-role",
        "/v1/chat/completions",
        {
            "messages": [
                {
                    "role": "tool",
                    "tool_call_id": "call_synthetic",
                    "content": "synthetic",
                }
            ]
        },
        "tools",
    ),
    (
        "assistant-tool-calls",
        "/v1/chat/completions",
        {
            "messages": [
                {"role": "assistant", "content": None, "tool_calls": []},
            ]
        },
        "tools",
    ),
    (
        "legacy-function-call",
        "/v1/chat/completions",
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "function_call": {"name": "synthetic", "arguments": "{}"},
                }
            ]
        },
        "tools",
    ),
    (
        "responses-function-call-output",
        "/v1/responses",
        {
            "input": [
                {"type": "function_call_output", "call_id": "call_synthetic", "output": "ok"}
            ]
        },
        "tools",
    ),
    (
        "chat-response-format",
        "/v1/chat/completions",
        {
            "messages": [{"role": "user", "content": "synthetic"}],
            "response_format": {"type": "json_object"},
        },
        "structured_output",
    ),
    (
        "responses-text-format",
        "/v1/responses",
        {"input": "synthetic", "text": {"format": {"type": "json_object"}}},
        "structured_output",
    ),
    (
        "non-text-modality",
        "/v1/chat/completions",
        {
            "messages": [{"role": "user", "content": "synthetic"}],
            "modalities": ["text", "audio"],
        },
        "multimodal",
    ),
    (
        "image-content-type",
        "/v1/chat/completions",
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "synthetic"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                    ],
                }
            ]
        },
        "multimodal",
    ),
    (
        "audio-content-key",
        "/v1/chat/completions",
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "input_audio", "input_audio": {"data": "AA=="}}],
                }
            ]
        },
        "multimodal",
    ),
    (
        "responses-file-key",
        "/v1/responses",
        {"input": [{"type": "input_file", "file_id": "file_synthetic"}]},
        "multimodal",
    ),
    (
        "video-content-key",
        "/v1/responses",
        {"input": [{"video_url": "data:video/mp4;base64,AA=="}]},
        "multimodal",
    ),
)


def _bootstrap(store, settings) -> tuple[str, str]:
    result = bootstrap(store, settings, "default", "Default", False, True, 30)
    return result["api_key"], result["model"]


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "X-Request-ID": "capability-test"}


def _capabilities_without(name: str) -> Capabilities:
    values = {
        "chat": True,
        "responses": True,
        "streaming": True,
        "tools": True,
        "structured_output": True,
        "multimodal": True,
    }
    values[name] = False
    return Capabilities(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True], ids=["complete", "stream"])
async def test_ordered_provider_attempts_share_one_deadline(store, settings, stream: bool) -> None:
    _, model_id = _bootstrap(store, settings)
    for provider_id in ("slow-first", "slow-second"):
        store.put_resource(
            "providers",
            Provider(id=provider_id, tenant_id="default", kind="mock", residency="local"),
        )
    store.put_resource(
        "routes",
        Route(
            id="default-default",
            tenant_id="default",
            model_id=model_id,
            targets=[
                RouteTarget(provider_id="slow-first", residency="local", priority=1),
                RouteTarget(provider_id="slow-second", residency="local", priority=2),
            ],
        ),
    )

    class SlowRetryableProvider:
        def __init__(self, provider_id: str) -> None:
            self.provider_id = provider_id
            self.calls: list[str] = []

        async def complete(self, endpoint, payload, context):
            self.calls.append(context.provider_id)
            await asyncio.sleep(0.05)
            raise ProviderFailure("synthetic retryable failure", "synthetic_retryable")

        async def prepare_stream(self, endpoint, payload, context):
            self.calls.append(context.provider_id)
            await asyncio.sleep(0.05)
            raise ProviderFailure("synthetic retryable failure", "synthetic_retryable")

    class Providers:
        def __init__(self) -> None:
            self.adapter = SlowRetryableProvider("synthetic")

        def create(self, _provider):
            return self.adapter

    providers = Providers()
    router = GatewayRouter(store, providers, request_deadline_seconds=0.02)
    resolved = router.resolve("default", model_id, "chat/completions", {"stream": stream})

    started = time.monotonic()
    with pytest.raises(GatewayError) as raised:
        if stream:
            await router.prepare_stream(
                resolved,
                "chat/completions",
                {"stream": True},
                "deadline-stream",
                "default",
            )
        else:
            await router.complete(
                resolved,
                "chat/completions",
                {"stream": False},
                "deadline-complete",
                "default",
            )
    elapsed = time.monotonic() - started

    assert raised.value.status_code == 504
    assert raised.value.code == "gateway_deadline_exceeded"
    assert raised.value.audit_provider == "slow-first"
    assert providers.adapter.calls == ["slow-first"]
    assert elapsed < 0.08


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("_case", "path", "payload", "missing_capability"),
    CAPABILITY_REQUESTS,
    ids=[case[0] for case in CAPABILITY_REQUESTS],
)
async def test_model_capability_detection_fails_closed(
    _case, path, payload, missing_capability, store, settings
) -> None:
    key, model_id = _bootstrap(store, settings)
    store.put_resource(
        "models",
        Model(
            id=model_id,
            tenant_id="default",
            upstream_model="mock-1",
            capabilities=_capabilities_without(missing_capability),
        ),
    )

    async with asgi_client(create_data_plane(settings, store)) as client:
        response = await client.post(
            path,
            headers=_headers(key),
            json={"model": model_id, **payload},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_capability"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("_case", "path", "payload", "missing_capability"),
    CAPABILITY_REQUESTS,
    ids=[case[0] for case in CAPABILITY_REQUESTS],
)
async def test_target_capability_detection_uses_the_same_requirements_for_fallback(
    _case, path, payload, missing_capability, store, settings
) -> None:
    key, model_id = _bootstrap(store, settings)
    store.put_resource(
        "models",
        Model(
            id=model_id,
            tenant_id="default",
            upstream_model="mock-1",
            capabilities=Capabilities(
                chat=True,
                responses=True,
                streaming=True,
                tools=True,
                structured_output=True,
                multimodal=True,
            ),
        ),
    )
    store.put_resource(
        "providers",
        Provider(
            id="limited",
            tenant_id="default",
            kind="mock",
            residency="local",
            native_responses=True,
        ),
    )
    store.put_resource(
        "routes",
        Route(
            id="default-default",
            tenant_id="default",
            model_id=model_id,
            required_residency="local",
            targets=[
                RouteTarget(
                    provider_id="limited",
                    residency="local",
                    priority=1,
                    capabilities=_capabilities_without(missing_capability),
                ),
                RouteTarget(
                    provider_id="default-mock",
                    residency="local",
                    priority=2,
                    capabilities=Capabilities(
                        chat=True,
                        responses=True,
                        streaming=True,
                        tools=True,
                        structured_output=True,
                        multimodal=True,
                    ),
                ),
            ],
        ),
    )

    async with asgi_client(create_data_plane(settings, store)) as client:
        response = await client.post(
            path,
            headers=_headers(key),
            json={"model": model_id, **payload},
        )

    assert response.status_code == 200
    assert response.headers["x-provider-id"] == "default-mock"
    assert "skip:limited:ineligible;select:default-mock" in str(store.list_audit())


@pytest.mark.asyncio
async def test_function_parameter_schema_is_not_structured_or_multimodal_output(store, settings) -> None:
    key, model_id = _bootstrap(store, settings)
    capabilities = Capabilities(
        chat=True,
        responses=True,
        streaming=True,
        tools=True,
        structured_output=False,
        multimodal=False,
    )
    store.put_resource(
        "models",
        Model(
            id=model_id,
            tenant_id="default",
            upstream_model="mock-1",
            capabilities=capabilities,
        ),
    )
    store.put_resource(
        "routes",
        Route(
            id="default-default",
            tenant_id="default",
            model_id=model_id,
            targets=[
                RouteTarget(
                    provider_id="default-mock",
                    residency="local",
                    capabilities=capabilities,
                )
            ],
        ),
    )
    request = {
        "model": model_id,
        "messages": [{"role": "user", "content": "synthetic"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "synthetic",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "response_format": {"type": "object"},
                            "image_url": {"type": "string"},
                            "text": {"type": "object", "format": "synthetic"},
                        },
                    },
                },
            }
        ],
    }

    async with asgi_client(create_data_plane(settings, store)) as client:
        response = await client.post(
            "/v1/chat/completions", headers=_headers(key), json=request
        )

    assert response.status_code == 200
    assert response.headers["x-provider-id"] == "default-mock"
