# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

import httpx
import yaml
from pydantic import ValidationError

from portable_ai_gateway.cache import create_cache
from portable_ai_gateway.config import Settings
from portable_ai_gateway.providers.base import ProviderContext
from portable_ai_gateway.providers.factory import ProviderFactory
from portable_ai_gateway.schemas import (
    ApiKeyCreate,
    Capabilities,
    ConfigurationDocument,
    ConfigurationMetadata,
    ConfigurationSpec,
    Model,
    Policy,
    Provider,
    Quota,
    Route,
    RouteTarget,
    Tenant,
)
from portable_ai_gateway.security import ApiKeyManager, AuthContext, AuthService
from portable_ai_gateway.service import AuditRecorder, validate_resource_links
from portable_ai_gateway.store import InMemoryStore, Store, create_store


def load_configuration(path: str | Path) -> ConfigurationDocument:
    try:
        with Path(path).open(encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read configuration: {exc}") from exc
    try:
        return ConfigurationDocument.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Configuration schema validation failed: {exc}") from exc


def validate_configuration(document: ConfigurationDocument, settings: Settings) -> None:
    scratch = InMemoryStore()
    scratch.initialize()
    try:
        for kind in ("tenants", "providers", "models", "routes", "policies", "quotas"):
            seen: set[str] = set()
            for resource in getattr(document.spec, kind):
                if resource.id in seen:
                    raise ValueError(f"Duplicate {kind} ID: {resource.id}")
                seen.add(resource.id)
                validate_resource_links(kind, resource, scratch, settings)
                scratch.put_resource(kind, resource)
    finally:
        scratch.close()


def apply_configuration(document: ConfigurationDocument, store: Store, settings: Settings) -> None:
    # Schema and cross-resource validation happens against a scratch store first;
    # reconcile then provides atomic upsert + prune in the target database.
    validate_configuration(document, settings)
    store.reconcile_configuration(document)


def export_configuration(store: Store, version: str | None = None) -> ConfigurationDocument:
    snapshot = store.configuration_snapshot()
    return ConfigurationDocument(
        metadata=ConfigurationMetadata(version=version or snapshot.config_version or "unversioned"),
        spec=ConfigurationSpec(
            tenants=[Tenant.model_validate(item) for item in snapshot.list_resources("tenants")],
            providers=[Provider.model_validate(item) for item in snapshot.list_resources("providers")],
            models=[Model.model_validate(item) for item in snapshot.list_resources("models")],
            routes=[Route.model_validate(item) for item in snapshot.list_resources("routes")],
            policies=[Policy.model_validate(item) for item in snapshot.list_resources("policies")],
            quotas=[Quota.model_validate(item) for item in snapshot.list_resources("quotas")],
        ),
    )


def write_configuration(document: ConfigurationDocument, destination: str, stdout: TextIO = sys.stdout) -> None:
    serialized = yaml.safe_dump(document.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    if destination == "-":
        stdout.write(serialized)
        return
    Path(destination).write_text(serialized, encoding="utf-8")


def bootstrap(
    store: Store,
    settings: Settings,
    tenant_id: str,
    tenant_name: str,
    sensitive: bool,
    issue_key: bool,
    expiry_days: int,
) -> dict[str, Any]:
    provider_id = f"{tenant_id}-mock"
    model_id = f"{tenant_id}-mock-chat"
    resources = (
        ("tenants", Tenant(id=tenant_id, name=tenant_name, sensitive=sensitive)),
        (
            "providers",
            Provider(
                id=provider_id,
                tenant_id=tenant_id,
                kind="mock",
                residency="local",
                native_responses=True,
                labels={"profile": "development"},
            ),
        ),
        (
            "models",
            Model(
                id=model_id,
                tenant_id=tenant_id,
                upstream_model="mock-1",
                capabilities=Capabilities(chat=True, responses=True, streaming=True),
            ),
        ),
        (
            "routes",
            Route(
                id=f"{tenant_id}-default",
                tenant_id=tenant_id,
                model_id=model_id,
                targets=[
                    RouteTarget(
                        provider_id=provider_id,
                        priority=10,
                        residency="local",
                        capabilities=Capabilities(
                            chat=True,
                            responses=True,
                            streaming=True,
                        ),
                    )
                ],
            ),
        ),
        ("policies", Policy(id=f"{tenant_id}-default", tenant_id=tenant_id, max_input_chars=100_000)),
        (
            "quotas",
            Quota(id=f"{tenant_id}-default", tenant_id=tenant_id, requests_per_minute=600, concurrent_requests=100),
        ),
    )
    key_request = None
    if issue_key and not sensitive:
        key_request = ApiKeyCreate(
            tenant_id=tenant_id,
            name="bootstrap",
            scopes=["models:read", "inference:write"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=expiry_days),
        )

    # Validate the entire self-contained graph before touching the target store.
    scratch = InMemoryStore()
    scratch.initialize()
    try:
        for kind, resource in resources:
            validate_resource_links(kind, resource, scratch, settings)
            scratch.put_resource(kind, resource)
    finally:
        scratch.close()

    result: dict[str, Any] = {"tenant_id": tenant_id, "model": model_id, "provider": provider_id}
    with store.mutation_transaction():
        for kind, resource in resources:
            validate_resource_links(kind, resource, store, settings)
            store.put_resource(kind, resource)
        if key_request is not None:
            record, raw = ApiKeyManager(store, settings.api_key_pepper).issue(key_request)
            result.update(
                {
                    "api_key_id": record["id"],
                    "api_key": raw,
                    "expires_at": record["expires_at"].isoformat(),
                }
            )
    return result


def doctor(store: Store, settings: Settings) -> dict[str, Any]:
    cache = create_cache(settings.cache_url)

    async def inspect_cache() -> bool:
        try:
            return await cache.ping()
        finally:
            await cache.close()

    configuration = store.configuration_snapshot()
    routes = configuration.list_resources("routes")
    providers = configuration.list_resources("providers")
    report = {
        "status": "ok",
        "database": "ready" if store.ping() else "unavailable",
        "cache": "ready" if asyncio.run(inspect_cache()) else "unavailable",
        "tenants": len(configuration.list_resources("tenants")),
        "models": len(configuration.list_resources("models")),
        "routes": len(routes),
        "providers": len(providers),
        "gitops_mode": settings.gitops_mode,
        "config_version": configuration.config_version or settings.config_version,
        "source_url": settings.source_url,
    }
    if not routes or not providers:
        report["status"] = "degraded"
    return report


def resolve_provider_check_model(store: Store, provider_id: str) -> str:
    candidates: set[str] = set()
    for route in store.list_resources("routes"):
        if not route.get("enabled", False):
            continue
        model = store.get_resource("models", route["model_id"])
        if model is None or not model.get("enabled", False):
            continue
        for target in route.get("targets", []):
            if target.get("provider_id") == provider_id:
                candidates.add(target.get("upstream_model") or model["upstream_model"])
    if not candidates:
        raise ValueError("Provider is not referenced by an active route; pass --upstream-model explicitly")
    if len(candidates) > 1:
        raise ValueError("Provider has multiple routed models; pass --upstream-model explicitly")
    return next(iter(candidates))


async def check_provider(
    store: Store,
    settings: Settings,
    provider_id: str,
    upstream_model: str | None = None,
) -> dict[str, Any]:
    provider = store.get_resource("providers", provider_id)
    if provider is None:
        raise ValueError(f"Provider not found: {provider_id}")
    validate_resource_links("providers", Provider.model_validate(provider), store, settings)
    check_model = upstream_model or resolve_provider_check_model(store, provider_id)
    async with httpx.AsyncClient(timeout=settings.upstream_timeout_seconds) as client:
        adapter = ProviderFactory(client).create(provider)
        result = await adapter.complete(
            "chat/completions",
            {"model": check_model, "messages": [{"role": "user", "content": "synthetic connectivity check"}]},
            ProviderContext("doctor", provider.get("tenant_id") or "system", provider_id),
        )
    return {
        "status": "ok",
        "provider": provider_id,
        "upstream_model": check_model,
        "upstream_status": result.status_code,
    }


def cli_operator(store: Store, settings: Settings) -> AuthContext:
    """Resolve the privileged CLI actor without ever accepting a token argument."""
    if settings.app_env != "production":
        return AuthContext("cli:local", None, frozenset({"*"}), "cli")

    token_value = os.getenv("PAG_CLI_OIDC_TOKEN") or None
    token_file = os.getenv("PAG_CLI_OIDC_TOKEN_FILE") or None
    if token_value and token_file:
        raise ValueError("Set only one of PAG_CLI_OIDC_TOKEN or PAG_CLI_OIDC_TOKEN_FILE")
    if token_file:
        try:
            token_value = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("Cannot read PAG_CLI_OIDC_TOKEN_FILE") from exc
    if not token_value or len(token_value) > 16_384 or any(character.isspace() for character in token_value):
        raise ValueError("Production CLI commands require a valid OIDC token secret")
    try:
        return AuthService(settings, store).require_admin(f"Bearer {token_value}")
    except Exception as exc:
        raise ValueError("Production CLI OIDC authorization failed") from exc


def execute_cli(args: Any, settings: Settings, stdout: TextIO = sys.stdout) -> int:
    if args.cli_group == "config" and args.config_action == "validate":
        document = load_configuration(args.file)
        validate_configuration(document, settings)
        stdout.write(json.dumps({"status": "valid", "version": document.metadata.version}) + "\n")
        return 0

    store = create_store(
        settings.database_url,
        postgres_pool_min_size=settings.postgres_pool_min_size,
        postgres_pool_max_size=settings.postgres_pool_max_size,
        postgres_pool_acquire_timeout_seconds=settings.postgres_pool_acquire_timeout_seconds,
    )
    try:
        audit = AuditRecorder(store, store.get_config_version() or settings.config_version)
        cli_actor = cli_operator(store, settings)
        if args.cli_group == "bootstrap":
            if settings.gitops_mode:
                raise ValueError("bootstrap is disabled in GitOps mode; apply the desired configuration instead")
            with store.mutation_transaction():
                result = bootstrap(
                    store,
                    settings,
                    args.tenant_id,
                    args.tenant_name,
                    args.sensitive,
                    not args.no_api_key,
                    args.api_key_expiry_days,
                )
                audit.record(
                    request_id=f"cli-{uuid.uuid4()}",
                    auth=cli_actor,
                    action="config.bootstrap",
                    status=200,
                    resource=args.tenant_id,
                    tenant_id=args.tenant_id,
                )
        elif args.cli_group == "config" and args.config_action == "apply":
            document = load_configuration(args.file)
            validate_configuration(document, settings)
            with store.mutation_transaction():
                apply_configuration(document, store, settings)
                result = {"status": "applied", "version": document.metadata.version}
                audit.record(
                    request_id=f"cli-{uuid.uuid4()}",
                    auth=cli_actor,
                    action="config.apply",
                    status=200,
                    resource="configuration",
                )
        elif args.cli_group == "config" and args.config_action == "export":
            document = export_configuration(store, args.version)
            audit.record(
                request_id=f"cli-{uuid.uuid4()}",
                auth=cli_actor,
                action="config.export",
                status=200,
                resource="configuration",
                config_version=document.metadata.version,
            )
            write_configuration(document, args.output, stdout)
            return 0
        elif args.cli_group == "doctor":
            result = doctor(store, settings)
            audit.record(
                request_id=f"cli-{uuid.uuid4()}",
                auth=cli_actor,
                action="system.doctor",
                status=200,
                resource="runtime",
                config_version=result["config_version"],
            )
        elif args.cli_group == "provider" and args.provider_action == "check":
            result = asyncio.run(
                check_provider(store, settings, args.provider_id, args.upstream_model)
            )
            audit.record(
                request_id=f"cli-{uuid.uuid4()}",
                auth=cli_actor,
                action="provider.check",
                status=200,
                resource=args.provider_id,
            )
        else:  # pragma: no cover - argparse protects this
            raise ValueError("Unsupported CLI command")
        stdout.write(json.dumps(result, default=str, sort_keys=True) + "\n")
        return 0
    finally:
        store.close()
