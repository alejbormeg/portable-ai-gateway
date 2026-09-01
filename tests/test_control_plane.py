# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import asgi_client, jwt_token
from portable_ai_gateway.apps import create_control_plane
from portable_ai_gateway.cli import bootstrap, export_configuration, write_configuration
from portable_ai_gateway.composition import GatewayComposition
from portable_ai_gateway.errors import GatewayError
from portable_ai_gateway.extensions import AUDIT_SPI_VERSION
from portable_ai_gateway.schemas import Provider, Tenant
from portable_ai_gateway.security import ApiKeyManager
from portable_ai_gateway.service import validate_provider_network


def _admin(settings, scope: str = "admin") -> dict[str, str]:
    token = jwt_token(settings.oidc_hs256_secret, tenant_id=None, scope=scope)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_admin_requires_oidc_and_crud_is_audited(store, settings) -> None:
    async with asgi_client(create_control_plane(settings, store)) as client:
        assert (await client.get("/admin/v1/tenants")).status_code == 401
        body = {"id": "acme", "name": "ACME", "sensitive": False}
        created = await client.post("/admin/v1/tenants", headers=_admin(settings), json=body)
        assert created.status_code == 201
        assert created.json()["schema_version"] == "pag/v1"
        listed = await client.get("/admin/v1/tenants", headers=_admin(settings))
        assert listed.json()["data"][0]["id"] == "acme"
        replaced = await client.put(
            "/admin/v1/tenants/acme",
            headers=_admin(settings),
            json={"id": "acme", "name": "ACME 2", "sensitive": False},
        )
        assert replaced.json()["name"] == "ACME 2"
        assert (await client.delete("/admin/v1/tenants/acme", headers=_admin(settings))).status_code == 204
    events = store.list_audit()
    actions = [event["action"] for event in events]
    assert {"tenants.create", "tenants.replace", "tenants.delete"}.issubset(actions)
    deleted = next(event for event in events if event["action"] == "tenants.delete")
    assert deleted["config_version"] == store.get_config_version()


@pytest.mark.asyncio
async def test_authenticated_admin_failures_keep_actor_status_and_error_code(store, settings) -> None:
    async with asgi_client(create_control_plane(settings, store)) as client:
        invalid = await client.post(
            "/admin/v1/tenants",
            headers=_admin(settings),
            json={"id": "invalid tenant", "name": "Invalid"},
        )
        assert invalid.status_code == 400
        insufficient = await client.get(
            "/admin/v1/tenants",
            headers=_admin(settings, "models:read"),
        )
        assert insufficient.status_code == 403

    events = store.list_audit()
    invalid_event = next(event for event in events if event["status"] == 400)
    denied_event = next(event for event in events if event["status"] == 403)
    assert invalid_event["actor"] == "test-user"
    assert invalid_event["action"] == "tenants.create"
    assert invalid_event["error_code"] == "invalid_resource"
    assert denied_event["actor"] == "test-user"
    assert denied_event["error_code"] == "insufficient_scope"


@pytest.mark.asyncio
async def test_admin_config_read_and_audit_share_one_captured_revision(
    store, settings, monkeypatch
) -> None:
    bootstrap(store, settings, "default", "Default", False, False, 30)
    app = create_control_plane(settings, store)
    original_snapshot = store.configuration_snapshot
    captured: dict[str, str] = {}

    def snapshot_then_concurrent_change():
        snapshot = original_snapshot()
        if not captured:
            captured["version"] = snapshot.config_version
            store.put_resource("tenants", Tenant(id="concurrent", name="Concurrent"))
        return snapshot

    monkeypatch.setattr(store, "configuration_snapshot", snapshot_then_concurrent_change)
    async with asgi_client(app) as client:
        response = await client.get("/admin/v1/tenants", headers=_admin(settings))

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["default"]
    assert store.get_resource("tenants", "concurrent") is not None
    assert store.get_config_version() != captured["version"]
    event = next(event for event in store.list_audit() if event["action"] == "tenants.list")
    assert event["config_version"] == captured["version"]


@pytest.mark.asyncio
async def test_admin_failure_audit_keeps_the_revision_captured_before_validation(
    store, settings, monkeypatch
) -> None:
    bootstrap(store, settings, "default", "Default", False, False, 30)
    app = create_control_plane(settings, store)
    original_snapshot = store.configuration_snapshot
    captured: dict[str, str] = {}

    def snapshot_then_concurrent_change():
        snapshot = original_snapshot()
        if not captured:
            captured["version"] = snapshot.config_version
            store.put_resource("tenants", Tenant(id="concurrent", name="Concurrent"))
        return snapshot

    monkeypatch.setattr(store, "configuration_snapshot", snapshot_then_concurrent_change)
    async with asgi_client(app) as client:
        response = await client.post(
            "/admin/v1/tenants",
            headers=_admin(settings),
            json={"id": "invalid tenant", "name": "Invalid"},
        )

    assert response.status_code == 400
    assert store.get_config_version() != captured["version"]
    event = next(event for event in store.list_audit() if event["status"] == 400)
    assert event["config_version"] == captured["version"]


@pytest.mark.asyncio
async def test_audit_reader_scope_is_separate_from_administration(store, settings) -> None:
    headers = _admin(settings, "audit:read")
    async with asgi_client(create_control_plane(settings, store)) as client:
        assert (await client.get("/admin/v1/audit", headers=headers)).status_code == 200
        assert (await client.get("/admin/v1/tenants", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_api_key_is_returned_once_rotated_and_revoked(store, settings) -> None:
    bootstrap(store, settings, "default", "Default", False, False, 30)
    async with asgi_client(create_control_plane(settings, store)) as client:
        expiry = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        issued = await client.post(
            "/admin/v1/keys",
            headers=_admin(settings),
            json={
                "tenant_id": "default",
                "name": "application",
                "scopes": ["inference:write", "models:read"],
                "expires_at": expiry,
            },
        )
        assert issued.status_code == 201
        assert issued.headers["cache-control"] == "no-store"
        key_id = issued.json()["id"]
        original = issued.json()["key"]
        listed = (await client.get("/admin/v1/keys", headers=_admin(settings))).json()["data"][0]
        assert "key" not in listed and "key_hash" not in listed
        rotated = await client.post(f"/admin/v1/keys/{key_id}/rotate", headers=_admin(settings))
        assert rotated.status_code == 200
        assert rotated.json()["key"] != original
        revoked = await client.post(f"/admin/v1/keys/{key_id}/revoke", headers=_admin(settings))
        assert revoked.json()["revoked_at"] is not None


@pytest.mark.asyncio
async def test_sensitive_tenant_cannot_receive_api_key(store, settings) -> None:
    bootstrap(store, settings, "sensitive", "Sensitive", True, False, 30)
    async with asgi_client(create_control_plane(settings, store)) as client:
        denied = await client.post(
            "/admin/v1/keys",
            headers=_admin(settings),
            json={
                "tenant_id": "sensitive",
                "name": "forbidden",
                "scopes": ["inference:write"],
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
        )
        assert denied.status_code == 400
        assert denied.json()["error"]["code"] == "oidc_required"


@pytest.mark.asyncio
async def test_gitops_mode_rejects_direct_config_mutations_but_allows_key_ops(store, settings) -> None:
    bootstrap(store, settings, "default", "Default", False, False, 30)
    write_configuration(export_configuration(store, "git-v1"), str(settings.config_path))
    gitops = replace(settings, gitops_mode=True)
    async with asgi_client(create_control_plane(gitops, store)) as client:
        rejected = await client.post(
            "/admin/v1/tenants", headers=_admin(settings), json={"id": "new", "name": "New"}
        )
        assert rejected.status_code == 409
        issued = await client.post(
            "/admin/v1/keys",
            headers=_admin(settings),
            json={
                "tenant_id": "default",
                "name": "operational",
                "scopes": ["models:read"],
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
        )
        assert issued.status_code == 201


def test_provider_ssrf_allowlist_is_fail_closed(settings) -> None:
    production = replace(settings, app_env="production", provider_host_allowlist=("llm.internal",))
    blocked = Provider(
        id="blocked", tenant_id="default", kind="openai-compatible", base_url="https://127.0.0.1/v1"
    )
    try:
        validate_provider_network(blocked, production)
        assert False, "private provider should have been blocked"
    except Exception as exc:
        assert "allowlist" in str(exc).lower()
    allowed = Provider(
        id="allowed", tenant_id="default", kind="openai-compatible", base_url="https://llm.internal/v1"
    )
    validate_provider_network(allowed, production)

    with pytest.raises(Exception):
        Provider(
            id="prefix-bypass",
            tenant_id="default",
            kind="openai-compatible",
            base_url="http://localhost.evil.example/v1",
        )
    with pytest.raises(Exception):
        Provider(
            id="vertex-host-injection",
            tenant_id="default",
            kind="vertex",
            project="customer-project",
            location="evil.example/x",
        )

    vertex = Provider(
        id="vertex",
        tenant_id="default",
        kind="vertex",
        project="customer-project",
        location="europe-west1",
    )
    with pytest.raises(Exception, match="ALLOWLIST"):
        validate_provider_network(vertex, production)
    validate_provider_network(
        vertex,
        replace(
            production,
            provider_host_allowlist=("europe-west1-aiplatform.googleapis.com",),
        ),
    )


@pytest.mark.asyncio
async def test_route_cannot_cross_tenant_boundaries(store, settings) -> None:
    bootstrap(store, settings, "one", "One", False, False, 30)
    bootstrap(store, settings, "two", "Two", False, False, 30)
    async with asgi_client(create_control_plane(settings, store)) as client:
        invalid = await client.post(
            "/admin/v1/routes",
            headers=_admin(settings),
            json={
                "id": "cross",
                "tenant_id": "one",
                "model_id": "one-mock-chat",
                "targets": [{"provider_id": "two-mock", "priority": 1}],
            },
        )
        assert invalid.status_code == 400
        assert invalid.json()["error"]["code"] == "invalid_provider"


@pytest.mark.asyncio
async def test_tenant_delete_cascades_configuration_and_permanently_revokes_keys(store, settings) -> None:
    bootstrapped = bootstrap(store, settings, "retired", "Retired", False, True, 30)
    manager = ApiKeyManager(store, settings.api_key_pepper)
    assert manager.authenticate(bootstrapped["api_key"]).tenant_id == "retired"

    async with asgi_client(create_control_plane(settings, store)) as client:
        deleted = await client.delete("/admin/v1/tenants/retired", headers=_admin(settings))
        assert deleted.status_code == 204
        recreated = await client.post(
            "/admin/v1/tenants",
            headers=_admin(settings),
            json={"id": "retired", "name": "Replacement", "sensitive": False},
        )
        assert recreated.status_code == 201

    assert store.list_resources("providers", "retired") == []
    assert store.list_resources("models", "retired") == []
    assert store.list_resources("routes", "retired") == []
    with pytest.raises(GatewayError, match="revoked"):
        manager.authenticate(bootstrapped["api_key"])


@pytest.mark.asyncio
async def test_control_plane_rejects_ambiguous_routes_and_dangling_provider_delete(store, settings) -> None:
    bootstrap(store, settings, "default", "Default", False, False, 30)
    async with asgi_client(create_control_plane(settings, store)) as client:
        duplicate = await client.post(
            "/admin/v1/routes",
            headers=_admin(settings),
            json={
                "id": "duplicate",
                "tenant_id": "default",
                "model_id": "default-mock-chat",
                "targets": [
                    {
                        "provider_id": "default-mock",
                        "priority": 1,
                        "residency": "local",
                        "capabilities": {
                            "chat": True,
                            "responses": True,
                            "streaming": True,
                        },
                    }
                ],
            },
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "ambiguous_route"

        in_use = await client.delete("/admin/v1/providers/default-mock", headers=_admin(settings))
        assert in_use.status_code == 409
        assert in_use.json()["error"]["code"] == "resource_in_use"


@pytest.mark.asyncio
async def test_admin_mutations_roll_back_when_audit_sink_fails(store, settings) -> None:
    bootstrapped = bootstrap(store, settings, "default", "Default", False, True, 30)
    key_id = bootstrapped["api_key_id"]
    original_key = store.get_api_key(key_id)
    original_version = store.get_config_version()

    class FailingAudit:
        spi_version = AUDIT_SPI_VERSION

        def emit(self, event):
            del event
            raise RuntimeError("audit unavailable")

    class FailingAuditComposition(GatewayComposition):
        def create_audit_sink(self, configured_store):
            del configured_store
            return FailingAudit()

    app = create_control_plane(settings, store, composition=FailingAuditComposition())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://gateway.test",
        ) as client:
            resource = await client.post(
                "/admin/v1/tenants",
                headers=_admin(settings),
                json={"id": "must-rollback", "name": "Must rollback"},
            )
            issued = await client.post(
                "/admin/v1/keys",
                headers=_admin(settings),
                json={
                    "id": "must-rollback",
                    "tenant_id": "default",
                    "name": "Must rollback",
                    "scopes": ["models:read"],
                    "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                },
            )
            rotated = await client.post(
                f"/admin/v1/keys/{key_id}/rotate",
                headers=_admin(settings),
            )
            revoked = await client.post(
                f"/admin/v1/keys/{key_id}/revoke",
                headers=_admin(settings),
            )

    assert [resource.status_code, issued.status_code, rotated.status_code, revoked.status_code] == [
        500,
        500,
        500,
        500,
    ]
    assert store.get_resource("tenants", "must-rollback") is None
    assert store.get_api_key("must-rollback") is None
    assert store.get_api_key(key_id) == original_key
    assert store.get_config_version() == original_version
