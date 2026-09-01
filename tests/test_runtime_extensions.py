# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
import yaml

from conftest import asgi_client, jwt_token
from portable_ai_gateway.apps import create_data_plane
from portable_ai_gateway.cache import InMemoryCache, QuotaEnforcer
from portable_ai_gateway.cli import bootstrap
from portable_ai_gateway.composition import GatewayComposition
from portable_ai_gateway.config import ConfigurationError
from portable_ai_gateway.extensions import (
    AUDIT_SPI_VERSION,
    AUTH_SPI_VERSION,
    OBSERVABILITY_SPI_VERSION,
    POLICY_SPI_VERSION,
    PROVIDER_SPI_VERSION,
    ROUTING_SPI_VERSION,
    PolicyDecision,
    PolicyExtension,
    validate_extension,
)
from portable_ai_gateway.providers.base import PreparedStream, ProviderResult
from portable_ai_gateway.security import ApiKeyManager, AuthContext
from portable_ai_gateway.store import InMemoryStore


def _mounted_config() -> dict:
    return {
        "api_version": "gateway.portable-ai.dev/v1alpha1",
        "kind": "GatewayConfiguration",
        "metadata": {"version": "mounted-v7"},
        "spec": {
            "tenants": [{"id": "default", "name": "Default"}],
            "providers": [
                {
                    "id": "mock",
                    "tenant_id": "default",
                    "kind": "mock",
                    "residency": "local",
                    "native_responses": True,
                }
            ],
            "models": [
                {
                    "id": "chat",
                    "tenant_id": "default",
                    "upstream_model": "mock-1",
                    "capabilities": {"chat": True, "responses": True, "streaming": True},
                }
            ],
            "routes": [
                {
                    "id": "default",
                    "tenant_id": "default",
                    "model_id": "chat",
                    "targets": [
                        {
                            "provider_id": "mock",
                            "priority": 1,
                            "residency": "local",
                            "capabilities": {
                                "chat": True,
                                "responses": True,
                                "streaming": True,
                            },
                        }
                    ],
                }
            ],
            "policies": [],
            "quotas": [],
        },
    }


@pytest.mark.asyncio
async def test_runtime_reconciles_mounted_config_and_uses_its_version(store, settings) -> None:
    settings.config_path.write_text(yaml.safe_dump(_mounted_config()), encoding="utf-8")
    gitops = replace(settings, gitops_mode=True)
    app = create_data_plane(gitops, store)
    token = jwt_token(settings.oidc_hs256_secret, tenant_id="default", scope="models:read inference:write")
    headers = {"Authorization": f"Bearer {token}"}
    async with asgi_client(app) as client:
        assert (await client.get("/ready")).status_code == 200
        response = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "chat", "messages": [{"role": "user", "content": "secret"}]},
        )
        assert response.status_code == 200
    assert store.get_resource("providers", "mock") is not None
    assert store.list_audit()[0]["config_version"] == "mounted-v7"


@pytest.mark.asyncio
async def test_non_gitops_mounted_config_only_seeds_an_empty_database(store, settings) -> None:
    settings.config_path.write_text(yaml.safe_dump(_mounted_config()), encoding="utf-8")
    first = create_data_plane(settings, store)
    async with asgi_client(first) as client:
        assert (await client.get("/ready")).status_code == 200

    store.put_resource("tenants", {"schema_version": "pag/v1", "id": "operator", "name": "Operator", "enabled": True, "labels": {}, "tenant_id": None, "sensitive": False})
    changed = _mounted_config()
    changed["spec"]["tenants"] = [{"id": "replacement", "name": "Replacement"}]
    changed["spec"]["providers"] = []
    changed["spec"]["models"] = []
    changed["spec"]["routes"] = []
    settings.config_path.write_text(yaml.safe_dump(changed), encoding="utf-8")

    second = create_data_plane(settings, store)
    async with asgi_client(second):
        pass
    assert store.get_resource("tenants", "operator") is not None
    assert store.get_resource("tenants", "replacement") is None


@pytest.mark.asyncio
async def test_non_gitops_seed_check_and_reconcile_share_the_mutation_boundary(settings) -> None:
    class TrackingStore(InMemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.seed_check_depths: list[int] = []
            self.reconcile_depth: int | None = None
            self.reconciled = False

        def list_resources(self, kind, tenant_id=None):
            if not self.reconciled and kind in {
                "tenants",
                "providers",
                "models",
                "routes",
                "policies",
                "quotas",
            }:
                self.seed_check_depths.append(self._transaction_depth)
            return super().list_resources(kind, tenant_id)

        def reconcile_configuration(self, document) -> None:
            self.reconcile_depth = self._transaction_depth
            self.reconciled = True
            super().reconcile_configuration(document)

    settings.config_path.write_text(yaml.safe_dump(_mounted_config()), encoding="utf-8")
    tracked = TrackingStore()
    tracked.initialize()
    try:
        app = create_data_plane(settings, tracked)
        async with asgi_client(app) as client:
            assert (await client.get("/ready")).status_code == 200
        assert tracked.seed_check_depths
        assert set(tracked.seed_check_depths) == {1}
        assert tracked.reconcile_depth == 1
    finally:
        tracked.close()


def test_gitops_missing_config_is_fail_closed(store, settings) -> None:
    with pytest.raises(ConfigurationError, match="mandatory"):
        create_data_plane(replace(settings, gitops_mode=True), store)


@pytest.mark.asyncio
async def test_concurrency_quota_is_shared_by_enforcers() -> None:
    cache = InMemoryCache()
    first = QuotaEnforcer(cache)
    second = QuotaEnforcer(cache)
    lease = first.concurrent("tenant", 1)
    await lease.__aenter__()
    try:
        with pytest.raises(Exception) as raised:
            async with second.concurrent("tenant", 1):
                pass
        assert getattr(raised.value, "code", None) == "tenant_concurrency_limit"
    finally:
        await lease.__aexit__(None, None, None)
    async with second.concurrent("tenant", 1):
        pass


@pytest.mark.asyncio
async def test_expired_concurrency_owner_cannot_release_a_new_lease(monkeypatch) -> None:
    cache = InMemoryCache()
    now = 100.0
    monkeypatch.setattr("portable_ai_gateway.cache.time.monotonic", lambda: now)
    old_token = await cache.acquire_limit("concurrent", 1, 1)
    assert old_token is not None
    now = 102.0
    new_token = await cache.acquire_limit("concurrent", 1, 10)
    assert new_token is not None and new_token != old_token
    await cache.release_limit("concurrent", old_token)
    assert await cache.acquire_limit("concurrent", 1, 10) is None


@pytest.mark.asyncio
async def test_active_concurrency_lease_is_renewed_before_its_original_expiry() -> None:
    cache = InMemoryCache()
    quotas = QuotaEnforcer(cache, concurrent_lease_seconds=1, renewal_interval_seconds=0.01)

    async with quotas.concurrent("tenant", 1) as lease:
        assert lease is not None
        await asyncio.sleep(0.03)
        lease.ensure_active()
        # The original lease would have expired after one second. Renewal keeps
        # the slot owned beyond that initial bound.
        await asyncio.sleep(1.05)
        assert await cache.acquire_limit("pag:quota:tenant:concurrent", 1, 1) is None


@pytest.mark.asyncio
async def test_lost_concurrency_lease_fails_closed_for_a_live_stream() -> None:
    class LostLeaseCache(InMemoryCache):
        async def renew_limit(self, key: str, token: str, ttl_seconds: int) -> bool:
            return False

    quotas = QuotaEnforcer(
        LostLeaseCache(), concurrent_lease_seconds=3, renewal_interval_seconds=0.01
    )
    async with quotas.concurrent("tenant", 1) as lease:
        assert lease is not None
        await asyncio.sleep(0.03)
        with pytest.raises(Exception) as raised:
            lease.ensure_active()
        assert getattr(raised.value, "code", None) == "tenant_concurrency_lease_lost"


def test_versioned_extension_contract_rejects_mismatch() -> None:
    class AllowAll:
        spi_version = POLICY_SPI_VERSION

        def evaluate(self, context, metadata):
            return PolicyDecision(True, "allowed")

    extension = AllowAll()
    validate_extension(extension, PolicyExtension, POLICY_SPI_VERSION)
    extension.spi_version = "pag.policy/v2"
    with pytest.raises(ValueError, match="pag.policy/v1"):
        validate_extension(extension, PolicyExtension, POLICY_SPI_VERSION)


def test_runtime_composition_root_is_versioned_and_injectable(store, settings) -> None:
    bootstrap(store, settings, "default", "Default", False, False, 7)

    class CustomerComposition(GatewayComposition):
        def __init__(self) -> None:
            self.calls: list[str] = []

        def create_authentication(self, configured, configured_store):
            self.calls.append("authentication")
            return super().create_authentication(configured, configured_store)

        def create_audit(self, configured_store, config_version):
            self.calls.append("audit")
            return super().create_audit(configured_store, config_version)

        def create_provider_factory(self, client):
            self.calls.append("provider")
            return super().create_provider_factory(client)

        def create_router(self, configured_store, providers):
            self.calls.append("routing-policy")
            return super().create_router(configured_store, providers)

        def configure_app(self, app, runtime):
            self.calls.append("observability")
            app.state.customer_observability = True

    composition = CustomerComposition()
    app = create_data_plane(settings, store, composition=composition)
    assert app.state.customer_observability is True
    assert composition.calls == [
        "authentication",
        "audit",
        "provider",
        "routing-policy",
        "observability",
    ]


@pytest.mark.asyncio
async def test_customer_composition_replaces_all_six_extension_families(store, settings) -> None:
    seeded = bootstrap(store, settings, "default", "Default", False, False, 7)

    class CustomerAuthentication:
        spi_version = AUTH_SPI_VERSION

        def __init__(self, api_keys):
            self.api_keys = api_keys
            self.calls = 0

        def authenticate(self, authorization):
            self.calls += 1
            assert authorization == "Bearer customer-token"
            return AuthContext(
                "customer-user",
                "default",
                frozenset({"models:read", "inference:write"}),
                "jwt",
            )

    class CustomerPolicy:
        spi_version = POLICY_SPI_VERSION

        def __init__(self):
            self.inputs = []

        def evaluate(self, context, evaluation):
            self.inputs.append((context, evaluation))
            evaluation.payload["messages"][0]["content"] = "policy-copy-mutated"
            return PolicyDecision(True, "customer_allowed")

    class CustomerRouting:
        spi_version = ROUTING_SPI_VERSION

        def __init__(self):
            self.inputs = []

        def order(self, context, candidates):
            self.inputs.append((context, candidates))
            return tuple(reversed(candidates))

    class CustomerAudit:
        spi_version = AUDIT_SPI_VERSION

        def __init__(self):
            self.events = []

        def emit(self, event):
            self.events.append(event)

    class CustomerObservability:
        spi_version = OBSERVABILITY_SPI_VERSION

        def __init__(self):
            self.requests = []
            self.providers = []

        async def request_finished(self, context, status_code, duration_ms):
            self.requests.append((context, status_code, duration_ms))

        async def provider_finished(self, context, provider_id, status_code, duration_ms):
            self.providers.append((context, provider_id, status_code, duration_ms))

    class CustomerProvider:
        spi_version = PROVIDER_SPI_VERSION

        def __init__(self):
            self.calls = []

        async def complete(self, endpoint, payload, context):
            self.calls.append(
                (endpoint, payload["model"], payload["messages"][0]["content"], context)
            )
            return ProviderResult(
                {
                    "id": "customer-result",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "customer provider"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )

        async def prepare_stream(self, endpoint, payload, context):
            async def remainder():
                if False:
                    yield b""

            return PreparedStream(b"data: customer\n\n", remainder())

    class CustomerProviderFactory:
        def __init__(self, adapter):
            self.adapter = adapter

        def create(self, provider):
            return self.adapter

    class CustomerComposition(GatewayComposition):
        def __init__(self):
            self.authentication = None
            self.policy = CustomerPolicy()
            self.routing = CustomerRouting()
            self.audit = CustomerAudit()
            self.observability = CustomerObservability()
            self.provider = CustomerProvider()

        def create_authentication_extension(self, configured, configured_store):
            self.authentication = CustomerAuthentication(
                ApiKeyManager(configured_store, configured.api_key_pepper)
            )
            return self.authentication

        def create_policy_extension(self):
            return self.policy

        def create_routing_extension(self):
            return self.routing

        def create_audit_sink(self, configured_store):
            return self.audit

        def create_observability_hook(self):
            return self.observability

        def create_provider_factory(self, client):
            return CustomerProviderFactory(self.provider)

    composition = CustomerComposition()
    app = create_data_plane(settings, store, composition=composition)
    async with asgi_client(app) as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer customer-token"},
            json={
                "model": seeded["model"],
                "messages": [{"role": "user", "content": "replace-six-families"}],
            },
        )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "customer provider"
    assert composition.authentication.calls == 1
    assert len(composition.policy.inputs) == 1
    assert composition.policy.inputs[0][1].endpoint == "chat/completions"
    assert composition.policy.inputs[0][1].parameter_names == ("messages", "model")
    assert composition.policy.inputs[0][1].payload["messages"][0]["content"] == "policy-copy-mutated"
    assert len(composition.routing.inputs) == 1
    assert composition.provider.calls[0][0] == "chat/completions"
    assert composition.provider.calls[0][2] == "replace-six-families"
    assert composition.audit.events[0].action == "chat.completions.create"
    assert composition.observability.providers[0][1:3] == ("default-mock", 200)
    assert composition.observability.requests[0][1] == 200


@pytest.mark.asyncio
async def test_customer_request_guardrail_denies_by_content_before_provider(store, settings) -> None:
    seeded = bootstrap(store, settings, "default", "Default", False, True, 7)

    class ContentGuardrail:
        spi_version = POLICY_SPI_VERSION

        def __init__(self):
            self.seen = []

        def evaluate(self, context, evaluation):
            content = evaluation.payload["messages"][0]["content"]
            denied = "customer-secret-block" in content
            self.seen.append((context.operation, evaluation.endpoint, denied))
            if denied:
                return PolicyDecision(False, "customer_content_denied")
            return PolicyDecision(True, "allowed")

    class GuardrailComposition(GatewayComposition):
        def __init__(self):
            self.policy = ContentGuardrail()

        def create_policy_extension(self):
            return self.policy

    composition = GuardrailComposition()
    async with asgi_client(create_data_plane(settings, store, composition=composition)) as client:
        denied = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {seeded['api_key']}"},
            json={
                "model": seeded["model"],
                "messages": [
                    {"role": "user", "content": "customer-secret-block"},
                ],
            },
        )

    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "customer_content_denied"
    assert composition.policy.seen == [
        ("chat/completions", "chat/completions", True)
    ]
    assert "customer-secret-block" not in str(store.list_audit())
