# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from conftest import asgi_client, jwt_token
from portable_ai_gateway.apps import create_data_plane
from portable_ai_gateway.cache import InMemoryCache
from portable_ai_gateway.cli import bootstrap
from portable_ai_gateway.schemas import (
    ApiKeyCreate,
    Capabilities,
    Model,
    Provider,
    Quota,
    Route,
    RouteTarget,
    Tenant,
)
from portable_ai_gateway.security import ApiKeyManager


def _bootstrap(store, settings) -> tuple[str, str]:
    result = bootstrap(store, settings, "default", "Default", False, True, 30)
    return result["api_key"], result["model"]


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "X-Request-ID": "test-request"}


@pytest.mark.asyncio
async def test_health_source_models_chat_responses_and_stream(store, settings) -> None:
    key, model = _bootstrap(store, settings)
    async with asgi_client(create_data_plane(settings, store)) as client:
        health = await client.get("/health")
        assert health.json()["source_url"] == settings.source_url
        assert health.headers["link"] == f'<{settings.source_url}>; rel="source"'
        assert (await client.get("/ready")).status_code == 200
        assert (await client.get("/v1/models")).status_code == 401
        models = await client.get("/v1/models", headers=_headers(key))
        assert models.json()["data"][0]["id"] == model

        chat = await client.post(
            "/v1/chat/completions",
            headers=_headers(key),
            json={"model": model, "messages": [{"role": "user", "content": "top secret"}]},
        )
        assert chat.status_code == 200
        assert chat.headers["cache-control"] == "no-store"
        assert chat.json()["choices"][0]["message"]["content"] == "mock response"
        assert chat.headers["x-provider-id"] == "default-mock"

        responses = await client.post(
            "/v1/responses",
            headers=_headers(key),
            json={"model": model, "input": "top secret"},
        )
        assert responses.status_code == 200
        assert responses.json()["object"] == "response"

        async with client.stream(
            "POST",
            "/v1/chat/completions",
            headers=_headers(key),
            json={"model": model, "messages": [{"role": "user", "content": "hi"}], "stream": True},
        ) as streamed:
            assert streamed.status_code == 200
            assert streamed.headers["cache-control"] == "no-store"
            text = "".join([part async for part in streamed.aiter_text()])
            assert "mock response" in text

        async with client.stream(
            "POST",
            "/v1/responses",
            headers=_headers(key),
            json={"model": model, "input": "hi", "stream": True},
        ) as response_stream:
            response_events = "".join([part async for part in response_stream.aiter_text()])
            event_types = [
                "response.created",
                "response.output_text.delta",
                "response.completed",
            ]
            positions = [response_events.index(f"event: {event_type}") for event_type in event_types]
            assert positions == sorted(positions)
            assert '"sequence_number":0' in response_events
            assert '"sequence_number":2' in response_events

    audit = str(store.list_audit())
    assert "top secret" not in audit
    assert "messages" not in audit
    assert "input" not in audit


@pytest.mark.asyncio
async def test_stream_audit_keeps_the_configuration_revision_captured_at_resolution(
    store, settings
) -> None:
    key, model = _bootstrap(store, settings)
    app = create_data_plane(settings, store)
    auth = app.state.runtime.auth.authenticate(f"Bearer {key}")
    resolved_version = store.get_config_version()

    outcome = await app.state.runtime.inference.infer(
        "chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": "synthetic"}],
            "stream": True,
        },
        auth,
        "stream-revision",
    )
    assert outcome.finalize_stream is not None
    assert outcome.release is not None
    _ = [chunk async for chunk in outcome.result.iter_bytes()]

    store.put_resource("tenants", Tenant(id="default", name="Changed after resolution"))
    assert store.get_config_version() != resolved_version
    outcome.finalize_stream(200, None)
    await outcome.release()

    event = next(item for item in store.list_audit() if item["request_id"] == "stream-revision")
    assert event["config_version"] == resolved_version


@pytest.mark.asyncio
async def test_ordered_fallback_happens_before_response(store, settings) -> None:
    key, model = _bootstrap(store, settings)
    store.put_resource(
        "providers",
        Provider(
            id="failing",
            tenant_id="default",
            kind="mock",
            native_responses=True,
            labels={"mock.fail": "true"},
        ),
    )
    store.put_resource(
        "routes",
        Route(
            id="default-default",
            tenant_id="default",
            model_id=model,
            targets=[
                RouteTarget(provider_id="failing", priority=1),
                RouteTarget(provider_id="default-mock", priority=2, residency="local"),
            ],
        ),
    )
    async with asgi_client(create_data_plane(settings, store)) as client:
        regular = await client.post(
            "/v1/chat/completions",
            headers=_headers(key),
            json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert regular.status_code == 200
        assert regular.headers["x-provider-id"] == "default-mock"
        streamed = await client.post(
            "/v1/chat/completions",
            headers=_headers(key),
            json={"model": model, "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )
        assert streamed.status_code == 200
        assert streamed.headers["x-provider-id"] == "default-mock"
    assert "fail:failing:mock_failure;select:default-mock" in str(store.list_audit())


@pytest.mark.asyncio
async def test_exhausted_provider_attempts_are_preserved_in_audit(store, settings) -> None:
    key, model = _bootstrap(store, settings)
    store.put_resource(
        "providers",
        Provider(
            id="failing",
            tenant_id="default",
            kind="mock",
            native_responses=True,
            labels={"mock.fail": "true"},
        ),
    )
    store.put_resource(
        "routes",
        Route(
            id="default-default",
            tenant_id="default",
            model_id=model,
            targets=[RouteTarget(provider_id="failing", priority=1)],
        ),
    )
    async with asgi_client(create_data_plane(settings, store)) as client:
        failed = await client.post(
            "/v1/chat/completions",
            headers=_headers(key),
            json={"model": model, "messages": [{"role": "user", "content": "synthetic"}]},
        )
    assert failed.status_code == 502
    event = store.list_audit()[0]
    assert event["provider"] == "failing"
    assert event["decision"] == "fail:failing:mock_failure"
    assert event["error_code"] == "mock_failure"


@pytest.mark.asyncio
async def test_responses_skips_non_native_adapter_and_never_emulates(store, settings) -> None:
    key, model = _bootstrap(store, settings)
    store.put_resource(
        "providers", Provider(id="chat-only", tenant_id="default", kind="mock", native_responses=False)
    )
    store.put_resource(
        "routes",
        Route(
            id="default-default",
            tenant_id="default",
            model_id=model,
            targets=[
                RouteTarget(
                    provider_id="chat-only",
                    priority=1,
                    capabilities={"chat": True, "responses": True, "streaming": True},
                ),
                RouteTarget(
                    provider_id="default-mock",
                    priority=2,
                    residency="local",
                    capabilities={"chat": True, "responses": True, "streaming": True},
                ),
            ],
        ),
    )
    app = create_data_plane(settings, store)
    async with asgi_client(app) as client:
        response = await client.post(
            "/v1/responses", headers=_headers(key), json={"model": model, "input": "hello"}
        )
        assert response.status_code == 200
        assert response.headers["x-provider-id"] == "default-mock"

        store.put_resource(
            "routes",
            Route(
                id="default-default",
                tenant_id="default",
                model_id=model,
                targets=[
                    RouteTarget(
                        provider_id="chat-only",
                        priority=1,
                        capabilities={"chat": True, "responses": True, "streaming": True},
                    )
                ],
            ),
        )
        unavailable = await client.post(
            "/v1/responses", headers=_headers(key), json={"model": model, "input": "hello"}
        )
        assert unavailable.status_code == 503
        assert unavailable.json()["error"]["code"] == "no_eligible_provider"


@pytest.mark.asyncio
async def test_sensitive_tenant_rejects_api_key_but_accepts_oidc(store, settings) -> None:
    key, model = _bootstrap(store, settings)
    store.put_resource("tenants", Tenant(id="default", name="Default", sensitive=True))
    async with asgi_client(create_data_plane(settings, store)) as client:
        denied = await client.get("/v1/models", headers=_headers(key))
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "oidc_required"
        token = jwt_token(settings.oidc_hs256_secret, tenant_id="default", scope="models:read inference:write")
        allowed = await client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
        assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_policy_quota_and_tenant_ownership_are_enforced(store, settings) -> None:
    key, model = _bootstrap(store, settings)
    store.put_resource(
        "quotas", Quota(id="default-default", tenant_id="default", requests_per_minute=1, concurrent_requests=2)
    )
    async with asgi_client(create_data_plane(settings, store)) as client:
        request = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
        assert (await client.post("/v1/chat/completions", headers=_headers(key), json=request)).status_code == 200
        limited = await client.post("/v1/chat/completions", headers=_headers(key), json=request)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "tenant_rate_limit"

    store.put_resource("tenants", Tenant(id="other", name="Other"))
    other_record, other_key = ApiKeyManager(store, settings.api_key_pepper).issue(
        ApiKeyCreate(
            tenant_id="other",
            name="other",
            scopes=["inference:write", "models:read"],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
    )
    assert other_record["tenant_id"] == "other"
    async with asgi_client(create_data_plane(settings, store)) as client:
        invisible = await client.post(
            "/v1/chat/completions",
            headers=_headers(other_key),
            json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert invisible.status_code == 404


@pytest.mark.asyncio
async def test_invalid_payload_and_request_limit(store, settings) -> None:
    key, model = _bootstrap(store, settings)
    small = replace(settings, max_request_bytes=100)
    async with asgi_client(create_data_plane(small, store)) as client:
        invalid = await client.post(
            "/v1/chat/completions", headers=_headers(key), json={"model": model, "messages": []}
        )
        assert invalid.status_code == 400
        invalid_stream = await client.post(
            "/v1/chat/completions",
            headers=_headers(key),
            json={"model": model, "messages": [{"role": "user", "content": "hi"}], "stream": "yes"},
        )
        assert invalid_stream.status_code == 400
        null_input = await client.post(
            "/v1/responses",
            headers=_headers(key),
            json={"model": model, "input": None},
        )
        assert null_input.status_code == 400
        oversized = await client.post(
            "/v1/chat/completions",
            headers=_headers(key),
            content=b"{" + b"x" * 200,
        )
        assert oversized.status_code == 413


@pytest.mark.asyncio
async def test_untrusted_model_alias_cannot_inject_audit_content(store, settings) -> None:
    key, _ = _bootstrap(store, settings)
    forged = "missing-model\nforged audit line"
    async with asgi_client(create_data_plane(settings, store)) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers=_headers(key),
            json={"model": forged, "messages": [{"role": "user", "content": "synthetic"}]},
        )
    assert response.status_code == 400
    assert forged not in str(store.list_audit())
    assert store.list_audit()[0]["model"] is None


@pytest.mark.asyncio
async def test_fallback_filters_target_capabilities_and_provider_residency(store, settings) -> None:
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
                    capabilities=Capabilities(chat=True, responses=True, streaming=True, tools=False),
                ),
                RouteTarget(
                    provider_id="default-mock",
                    residency="local",
                    priority=2,
                    capabilities=Capabilities(chat=True, responses=True, streaming=True, tools=True),
                ),
            ],
        ),
    )
    async with asgi_client(create_data_plane(settings, store)) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers=_headers(key),
            json={
                "model": model_id,
                "messages": [{"role": "user", "content": "synthetic"}],
                "tools": [{"type": "function", "function": {"name": "noop"}}],
            },
        )
        multimodal = await client.post(
            "/v1/chat/completions",
            headers=_headers(key),
            json={
                "model": model_id,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": "https://example.test/x"}}],
                    }
                ],
            },
        )
    assert response.status_code == 200
    assert response.headers["x-provider-id"] == "default-mock"
    assert multimodal.status_code == 400
    assert multimodal.json()["error"]["code"] == "unsupported_capability"
    assert "skip:limited:ineligible;select:default-mock" in str(store.list_audit())


@pytest.mark.asyncio
async def test_auth_happens_before_body_and_chunked_body_is_bounded(store, settings) -> None:
    key, model = _bootstrap(store, settings)
    consumed = False

    async def unauthenticated_body():
        nonlocal consumed
        consumed = True
        yield b"{" + b"x" * 1_000

    small = replace(settings, max_request_bytes=100)
    async with asgi_client(create_data_plane(small, store)) as client:
        denied = await client.post(
            "/v1/chat/completions",
            content=unauthenticated_body(),
            headers={"Content-Type": "application/json"},
        )
        assert denied.status_code == 401
        assert consumed is False

        async def oversized_chunks():
            yield b'{"model":"' + model.encode() + b'","messages":[{"role":"user","content":"'
            yield b"x" * 120 + b'"}]}'

        oversized = await client.post(
            "/v1/chat/completions",
            headers={**_headers(key), "Content-Type": "application/json"},
            content=oversized_chunks(),
        )
        assert oversized.status_code == 413
        assert oversized.json()["error"]["code"] == "payload_too_large"


@pytest.mark.asyncio
async def test_quota_backend_failure_is_a_controlled_dependency_error(store, settings) -> None:
    class FailingCache(InMemoryCache):
        async def increment(self, key: str, ttl_seconds: int) -> int:
            raise ConnectionError("synthetic cache outage")

    key, model = _bootstrap(store, settings)
    async with asgi_client(create_data_plane(settings, store, cache=FailingCache())) as client:
        failed = await client.post(
            "/v1/chat/completions",
            headers=_headers(key),
            json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        )
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "quota_backend_unavailable"
