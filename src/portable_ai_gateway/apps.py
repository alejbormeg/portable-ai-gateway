# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from portable_ai_gateway import __version__
from portable_ai_gateway.cache import Cache, create_cache
from portable_ai_gateway.composition import GatewayComposition
from portable_ai_gateway.config import ConfigurationError, Settings
from portable_ai_gateway.errors import GatewayError, install_error_handlers
from portable_ai_gateway.extensions import ExtensionRequestContext, ObservabilityHook
from portable_ai_gateway.providers.base import PreparedStream, ProviderResult
from portable_ai_gateway.routing import GatewayRouter
from portable_ai_gateway.schemas import (
    ApiKeyCreate,
    ChatCompletionRequest,
    RESOURCE_MODELS,
    Resource,
    ResponsesRequest,
)
from portable_ai_gateway.security import AuthContext, public_key_view
from portable_ai_gateway.service import (
    InferenceService,
    validate_chat_payload,
    validate_resource_deletion,
    validate_resource_links,
)
from portable_ai_gateway.store import Store, create_store


LOGGER = logging.getLogger("portable_ai_gateway.requests")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(slots=True)
class Runtime:
    settings: Settings
    store: Store
    cache: Cache
    auth: Any
    audit: Any
    observability: ObservabilityHook
    client: httpx.AsyncClient
    inference: InferenceService
    composition: GatewayComposition
    owns_store: bool
    owns_cache: bool


def _runtime(
    settings: Settings,
    store: Store | None,
    cache: Cache | None,
    transport: httpx.AsyncBaseTransport | None,
    composition: GatewayComposition | None,
) -> Runtime:
    active_composition = composition or GatewayComposition()
    if getattr(active_composition, "spi_version", None) != "pag.composition/v1":
        raise ConfigurationError("Runtime composition requires pag.composition/v1")
    runtime_store = store or create_store(
        settings.database_url,
        postgres_pool_min_size=settings.postgres_pool_min_size,
        postgres_pool_max_size=settings.postgres_pool_max_size,
        postgres_pool_acquire_timeout_seconds=settings.postgres_pool_acquire_timeout_seconds,
    )
    runtime_cache = cache or create_cache(settings.cache_url)
    applied_version = settings.config_version
    if settings.config_path.is_file():
        # Imported lazily to keep the CLI and HTTP composition roots independent.
        from portable_ai_gateway.cli import apply_configuration, load_configuration, validate_configuration

        desired = load_configuration(settings.config_path)
        validate_configuration(desired, settings)
        # The non-GitOps empty check and optional seed are one serialized
        # decision. Otherwise two replicas can both observe an empty database
        # and let the later reconcile overwrite the first replica's seed.
        with runtime_store.mutation_transaction():
            has_persisted_configuration = any(
                runtime_store.list_resources(kind)
                for kind in ("tenants", "providers", "models", "routes", "policies", "quotas")
            )
            if settings.gitops_mode or not has_persisted_configuration:
                apply_configuration(desired, runtime_store, settings)
            applied_version = runtime_store.get_config_version() or desired.metadata.version
    elif settings.gitops_mode:
        raise ConfigurationError(
            f"PAG_CONFIG_PATH does not exist: {settings.config_path}; configuration is mandatory in GitOps mode"
        )
    else:
        applied_version = runtime_store.get_config_version() or applied_version
    client = httpx.AsyncClient(timeout=settings.upstream_timeout_seconds, transport=transport)
    auth = active_composition.create_authentication(settings, runtime_store)
    audit = active_composition.create_audit(runtime_store, applied_version)
    observability = active_composition.create_observability()
    providers = active_composition.observe_provider_factory(
        active_composition.create_provider_factory(client),
        observability,
    )
    for configured_provider in runtime_store.list_resources("providers"):
        if configured_provider.get("enabled", False):
            providers.create(configured_provider)
    router = active_composition.create_router(runtime_store, providers)
    if isinstance(router, GatewayRouter):
        # One request budget covers ordered pre-response fallback attempts.
        router.request_deadline_seconds = settings.upstream_timeout_seconds
    inference = active_composition.create_inference(
        runtime_store,
        router,
        runtime_cache,
        audit,
    )
    return Runtime(
        settings=settings,
        store=runtime_store,
        cache=runtime_cache,
        auth=auth,
        audit=audit,
        observability=observability,
        client=client,
        inference=inference,
        composition=active_composition,
        owns_store=store is None,
        owns_cache=cache is None,
    )


def _base_app(title: str, runtime: Runtime, require_routes: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await runtime.client.aclose()
            if runtime.owns_cache:
                await runtime.cache.close()
            if runtime.owns_store:
                runtime.store.close()

    app = FastAPI(title=title, version=__version__, lifespan=lifespan)
    app.state.runtime = runtime
    install_error_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        supplied = request.headers.get("X-Request-ID")
        request_id = supplied if supplied and REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.monotonic()
        status = 500
        response = None
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            if runtime.settings.source_url:
                response.headers["Link"] = f'<{runtime.settings.source_url}>; rel="source"'
            if request.url.path.startswith(("/v1/", "/admin/v1/")):
                # Provider-controlled cache headers must never make tenant data or
                # one-time credentials reusable by an intermediary.
                response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            duration_ms = (time.monotonic() - started) * 1000
            LOGGER.info(
                "request_completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "latency_ms": round(duration_ms, 2),
                },
            )
            auth = getattr(request.state, "extension_auth", None)
            route = request.scope.get("route")
            route_template = getattr(route, "path", "unmatched")
            context = ExtensionRequestContext(
                request_id=request_id,
                tenant_id=auth.tenant_id if auth is not None else None,
                actor=auth.actor if auth is not None else None,
                operation=getattr(request.state, "extension_operation", "http.request"),
                attributes={"method": request.method, "route": route_template},
            )
            try:
                await runtime.observability.request_finished(context, status, duration_ms)
            except Exception:
                LOGGER.warning("request_observability_hook_failed", exc_info=True)

    @app.get("/", tags=["system"])
    async def index() -> dict[str, Any]:
        return {"name": "Portable AI Gateway", "version": __version__, "source_url": runtime.settings.source_url}

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, Any]:
        return {"status": "ok", "source_url": runtime.settings.source_url}

    @app.get("/ready", tags=["system"])
    async def ready() -> dict[str, str]:
        try:
            store_ready = runtime.store.ping()
            cache_ready = await runtime.cache.ping()
        except Exception as exc:
            raise GatewayError(503, "Gateway is not ready", "server_error", "not_ready") from exc
        if not store_ready or not cache_ready:
            raise GatewayError(503, "Gateway is not ready", "server_error", "not_ready")
        if require_routes:
            active_routes = any(item.get("enabled", False) for item in runtime.store.list_resources("routes"))
            active_providers = any(item.get("enabled", False) for item in runtime.store.list_resources("providers"))
            if not active_routes or not active_providers:
                raise GatewayError(503, "Gateway has no active route", "server_error", "not_ready")
        return {"status": "ready"}

    return app


def create_data_plane(
    settings: Settings | None = None,
    store: Store | None = None,
    cache: Cache | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    composition: GatewayComposition | None = None,
) -> FastAPI:
    configured = settings or Settings.from_env()
    runtime = _runtime(configured, store, cache, transport, composition)
    app = _base_app("Portable AI Gateway - Data Plane", runtime, require_routes=True)
    runtime.composition.configure_app(app, runtime)

    def audit_failure(
        request: Request,
        auth: AuthContext,
        action: str,
        exc: GatewayError,
        model: str | None = None,
    ) -> None:
        runtime.audit.record(
            request_id=request.state.request_id,
            auth=auth,
            action=action,
            status=exc.status_code,
            model=model,
            decision="deny" if exc.error_type in {"authentication_error", "permission_error", "policy_error"} else "error",
            error_code=exc.code,
            config_version=getattr(request.state, "inference_config_version", None),
        )

    def authenticate(request: Request, action: str) -> AuthContext:
        request.state.extension_operation = action
        try:
            context = runtime.auth.authenticate(request.headers.get("Authorization"))
            request.state.extension_auth = context
            return context
        except GatewayError as exc:
            request.state.extension_auth = AuthContext(
                "unauthenticated",
                None,
                frozenset(),
                "anonymous",
            )
            audit_failure(
                request,
                AuthContext("unauthenticated", None, frozenset(), "anonymous"),
                action,
                exc,
            )
            raise

    def enforce_tenant_auth(auth: AuthContext, configuration: Any = None) -> None:
        if auth.tenant_id is None:
            raise GatewayError(403, "A tenant claim is required", "permission_error", "tenant_required")
        source = configuration or runtime.store
        tenant = source.get_resource("tenants", auth.tenant_id)
        if tenant is None or not tenant.get("enabled", False):
            raise GatewayError(403, "Tenant is unavailable", "permission_error", "tenant_unavailable")
        if tenant.get("sensitive", False) and auth.method == "api-key":
            raise GatewayError(403, "Sensitive tenants require OIDC", "permission_error", "oidc_required")

    @app.get("/v1/models", tags=["inference"])
    async def models(request: Request) -> dict[str, Any]:
        configuration = runtime.store.configuration_snapshot()
        request.state.inference_config_version = runtime.audit.effective_config_version(
            configuration.config_version
        )
        auth = authenticate(request, "models.list")
        try:
            auth.require("models:read")
            enforce_tenant_auth(auth, configuration)
        except GatewayError as exc:
            audit_failure(request, auth, "models.list", exc)
            raise
        routed_models = {
            item["model_id"]
            for item in configuration.list_resources("routes", auth.tenant_id)
            if item.get("enabled", False)
        }
        visible = [
            model
            for model in configuration.list_resources("models")
            if model["id"] in routed_models
            and model.get("enabled", False)
            and model.get("tenant_id") in {None, auth.tenant_id}
        ]
        runtime.audit.record(
            request_id=request.state.request_id,
            auth=auth,
            action="models.list",
            status=200,
            resource="models",
            config_version=request.state.inference_config_version,
        )
        return {
            "object": "list",
            "data": [{"id": model["id"], "object": "model", "owned_by": "customer"} for model in visible],
        }

    @app.post("/v1/chat/completions", tags=["inference"])
    async def chat_completions(request: Request) -> Any:
        action = "chat.completions.create"
        request.state.inference_config_version = runtime.audit.effective_config_version(
            runtime.store.configuration_snapshot().config_version
        )
        auth = authenticate(request, action)
        try:
            payload = await _read_json(request, configured.max_request_bytes)
            ChatCompletionRequest.model_validate(payload)
            validate_chat_payload(payload)
        except ValidationError as exc:
            invalid = GatewayError(400, "Chat request is invalid", "invalid_request_error", "invalid_request")
            audit_failure(request, auth, action, invalid)
            raise invalid from exc
        except GatewayError as exc:
            audit_failure(request, auth, action, exc)
            raise
        return await _infer_response(runtime, request, "chat/completions", payload, auth)

    @app.post("/v1/responses", tags=["inference"])
    async def responses(request: Request) -> Any:
        action = "responses.create"
        request.state.inference_config_version = runtime.audit.effective_config_version(
            runtime.store.configuration_snapshot().config_version
        )
        auth = authenticate(request, action)
        try:
            payload = await _read_json(request, configured.max_request_bytes)
            ResponsesRequest.model_validate(payload)
        except ValidationError as exc:
            invalid = GatewayError(400, "Responses request is invalid", "invalid_request_error", "invalid_request")
            audit_failure(request, auth, action, invalid)
            raise invalid from exc
        except GatewayError as exc:
            audit_failure(request, auth, action, exc)
            raise
        return await _infer_response(runtime, request, "responses", payload, auth)

    return app


async def _infer_response(
    runtime: Runtime,
    request: Request,
    endpoint: Any,
    payload: dict[str, Any],
    auth: AuthContext,
) -> Any:
    outcome = await runtime.inference.infer(endpoint, payload, auth, request.state.request_id)
    headers = {"X-Provider-ID": outcome.provider_id, "X-Route-ID": outcome.route_id}
    if isinstance(outcome.result, PreparedStream):
        headers.update(outcome.result.headers or {})
        headers.update({"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

        async def stream() -> AsyncIterator[bytes]:
            status = outcome.result.status_code
            error_code: str | None = None
            try:
                async for chunk in outcome.result.iter_bytes():
                    if outcome.ensure_lease_active:
                        outcome.ensure_lease_active()
                    yield chunk
            except asyncio.CancelledError:
                status = 499
                error_code = "stream_cancelled"
                raise
            except GatewayError as exc:
                status = exc.status_code
                error_code = exc.code
                raise
            except Exception:
                status = 502
                error_code = "stream_interrupted"
                raise
            finally:
                try:
                    if outcome.finalize_stream:
                        outcome.finalize_stream(status, error_code)
                finally:
                    if outcome.release:
                        await outcome.release()

        return StreamingResponse(
            stream(),
            status_code=outcome.result.status_code,
            media_type="text/event-stream",
            headers=headers,
        )
    result: ProviderResult = outcome.result
    headers.update(result.headers or {})
    return JSONResponse(result.payload, status_code=result.status_code, headers=headers)


def create_control_plane(
    settings: Settings | None = None,
    store: Store | None = None,
    cache: Cache | None = None,
    composition: GatewayComposition | None = None,
) -> FastAPI:
    configured = settings or Settings.from_env()
    runtime = _runtime(configured, store, cache, None, composition)
    app = _base_app("Portable AI Gateway - Control Plane", runtime)
    runtime.composition.configure_app(app, runtime)

    def capture_admin_configuration(request: Request, *, refresh: bool = False) -> Any:
        snapshot = getattr(request.state, "admin_configuration_snapshot", None)
        if snapshot is None or refresh:
            snapshot = runtime.store.configuration_snapshot()
            request.state.admin_configuration_snapshot = snapshot
            request.state.admin_config_version = runtime.audit.effective_config_version(
                snapshot.config_version
            )
        return snapshot

    def admin(request: Request, action: str, delegated_scope: str | None = None) -> AuthContext:
        request.state.extension_operation = action
        capture_admin_configuration(request)
        try:
            authorization = request.headers.get("Authorization")
            if not authorization:
                raise GatewayError(
                    401,
                    "Invalid or missing bearer token",
                    "authentication_error",
                    "invalid_authentication",
                    {"WWW-Authenticate": "Bearer"},
                )
            context = runtime.auth.authenticate(authorization)
        except GatewayError as exc:
            request.state.admin_audit_recorded = True
            runtime.audit.record(
                request_id=request.state.request_id,
                auth=AuthContext("unauthenticated", None, frozenset(), "anonymous"),
                action=action,
                status=exc.status_code,
                decision="deny",
                error_code=exc.code,
                config_version=request.state.admin_config_version,
            )
            request.state.extension_auth = AuthContext(
                "unauthenticated",
                None,
                frozenset(),
                "anonymous",
            )
            raise
        request.state.admin_auth = context
        request.state.extension_auth = context
        request.state.admin_action = action
        try:
            if context.method != "jwt":
                raise GatewayError(403, "Administration requires OIDC", "permission_error", "oidc_required")
            accepted = {"admin", "admin:*", "*"}
            if delegated_scope:
                accepted.add(delegated_scope)
            if accepted.isdisjoint(context.scopes):
                raise GatewayError(
                    403,
                    "Administrator scope is required",
                    "permission_error",
                    "insufficient_scope",
                )
        except GatewayError as exc:
            request.state.admin_audit_recorded = True
            runtime.audit.record(
                request_id=request.state.request_id,
                auth=context,
                action=action,
                status=exc.status_code,
                decision="deny",
                error_code=exc.code,
                config_version=request.state.admin_config_version,
            )
            raise
        return context

    def failed_admin_action(request: Request) -> tuple[str, str]:
        segments = [segment for segment in request.url.path.removeprefix("/admin/v1/").split("/") if segment]
        if not segments:
            return "admin.request", "admin"
        resource = segments[0]
        if resource == "audit":
            return "audit.list", "audit"
        if resource == "keys":
            if len(segments) >= 3 and segments[2] in {"rotate", "revoke"}:
                return f"keys.{segments[2]}", "keys"
            return ("keys.list" if request.method == "GET" and len(segments) == 1 else
                    "keys.get" if request.method == "GET" else "keys.issue"), "keys"
        if resource not in RESOURCE_MODELS:
            return "admin.request", "admin"
        operation = {
            "GET": "list" if len(segments) == 1 else "get",
            "POST": "create",
            "PUT": "replace",
            "DELETE": "delete",
        }.get(request.method, "request")
        return f"{resource}.{operation}", resource

    @app.middleware("http")
    async def audit_failed_admin_request(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        if (
            request.url.path.startswith("/admin/v1")
            and response.status_code >= 400
            and not getattr(request.state, "admin_audit_recorded", False)
        ):
            action, resource = failed_admin_action(request)
            auth = getattr(request.state, "admin_auth", None)
            if auth is None:
                try:
                    auth = runtime.auth.authenticate(request.headers.get("Authorization"))
                    request.state.extension_auth = auth
                except GatewayError:
                    auth = AuthContext("unauthenticated", None, frozenset(), "anonymous")
                    request.state.extension_auth = auth
            request.state.admin_audit_recorded = True
            if not hasattr(request.state, "admin_config_version"):
                capture_admin_configuration(request)
            runtime.audit.record(
                request_id=request.state.request_id,
                auth=auth,
                action=getattr(request.state, "admin_action", action),
                status=response.status_code,
                resource=resource,
                decision="deny" if response.status_code < 500 else "error",
                error_code=getattr(request.state, "gateway_error_code", "request_failed"),
                config_version=request.state.admin_config_version,
            )
        return response

    def mutable_config() -> None:
        if configured.gitops_mode:
            raise GatewayError(
                409,
                "Configuration is read-only in GitOps mode",
                "conflict_error",
                "gitops_read_only",
            )

    def audit_action(
        request: Request,
        auth: AuthContext,
        action: str,
        resource: str,
        *,
        status: int = 200,
        tenant_id: str | None = None,
    ) -> None:
        request.state.admin_audit_recorded = True
        runtime.audit.record(
            request_id=request.state.request_id,
            auth=auth,
            action=action,
            status=status,
            resource=resource,
            tenant_id=tenant_id,
            config_version=request.state.admin_config_version,
        )

    @app.get("/admin/v1/audit", tags=["administration"])
    async def list_audit(request: Request, tenant_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        auth = admin(request, "audit.list", "audit:read")
        data = runtime.store.list_audit(tenant_id, limit)
        audit_action(request, auth, "audit.list", "audit", tenant_id=tenant_id)
        return {"object": "list", "data": data}

    @app.get("/admin/v1/keys", tags=["administration"])
    async def list_keys(request: Request, tenant_id: str | None = None) -> dict[str, Any]:
        auth = admin(request, "keys.list")
        data = [public_key_view(item) for item in runtime.store.list_api_keys(tenant_id)]
        audit_action(request, auth, "keys.list", "keys", tenant_id=tenant_id)
        return {"object": "list", "data": data}

    @app.post("/admin/v1/keys", status_code=201, tags=["administration"])
    async def issue_key(request: Request) -> dict[str, Any]:
        auth = admin(request, "keys.issue")
        body = await _read_json(request, configured.max_request_bytes)
        try:
            key_request = ApiKeyCreate.model_validate(body)
        except ValidationError as exc:
            raise GatewayError(400, "API key request is invalid", "invalid_request_error", "invalid_key") from exc
        with runtime.store.mutation_transaction():
            configuration = capture_admin_configuration(request, refresh=True)
            tenant = configuration.get_resource("tenants", key_request.tenant_id)
            if tenant is None:
                raise GatewayError(400, "Tenant does not exist", "invalid_request_error", "invalid_tenant")
            if tenant.get("sensitive", False):
                raise GatewayError(400, "Sensitive tenants cannot use API keys", "invalid_request_error", "oidc_required")
            record, raw_key = runtime.auth.api_keys.issue(key_request)
            audit_action(
                request,
                auth,
                "keys.issue",
                record["id"],
                status=201,
                tenant_id=record["tenant_id"],
            )
        return {**public_key_view(record), "key": raw_key}

    @app.get("/admin/v1/keys/{key_id}", tags=["administration"])
    async def get_key(key_id: str, request: Request) -> dict[str, Any]:
        auth = admin(request, "keys.get")
        record = runtime.store.get_api_key(key_id)
        if record is None:
            raise GatewayError(404, "API key not found", "invalid_request_error", "key_not_found")
        audit_action(request, auth, "keys.get", key_id, tenant_id=record["tenant_id"])
        return public_key_view(record)

    @app.post("/admin/v1/keys/{key_id}/rotate", tags=["administration"])
    async def rotate_key(key_id: str, request: Request) -> dict[str, Any]:
        auth = admin(request, "keys.rotate")
        with runtime.store.mutation_transaction():
            capture_admin_configuration(request, refresh=True)
            record, raw_key = runtime.auth.api_keys.rotate(key_id)
            audit_action(request, auth, "keys.rotate", key_id, tenant_id=record["tenant_id"])
        return {**public_key_view(record), "key": raw_key}

    @app.post("/admin/v1/keys/{key_id}/revoke", tags=["administration"])
    async def revoke_key(key_id: str, request: Request) -> dict[str, Any]:
        auth = admin(request, "keys.revoke")
        with runtime.store.mutation_transaction():
            capture_admin_configuration(request, refresh=True)
            record = runtime.auth.api_keys.revoke(key_id)
            audit_action(request, auth, "keys.revoke", key_id, tenant_id=record["tenant_id"])
        return public_key_view(record)

    @app.get("/admin/v1/{resource}", tags=["administration"])
    async def list_resources(resource: str, request: Request, tenant_id: str | None = None) -> dict[str, Any]:
        auth = admin(request, f"{resource}.list")
        _resource_model(resource)
        data = capture_admin_configuration(request).list_resources(resource, tenant_id)
        audit_action(request, auth, f"{resource}.list", resource, tenant_id=tenant_id)
        return {"object": "list", "data": data}

    @app.post("/admin/v1/{resource}", status_code=201, tags=["administration"])
    async def create_resource(resource: str, request: Request) -> dict[str, Any]:
        auth = admin(request, f"{resource}.create")
        mutable_config()
        parsed = await _resource_body(resource, request, configured.max_request_bytes)
        target_tenant = parsed.id if resource == "tenants" else getattr(parsed, "tenant_id", None)
        with runtime.store.mutation_transaction():
            capture_admin_configuration(request, refresh=True)
            if runtime.store.get_resource(resource, parsed.id) is not None:
                raise GatewayError(409, "Resource already exists", "conflict_error", "resource_exists")
            validate_resource_links(resource, parsed, runtime.store, configured)
            result = runtime.store.put_resource(resource, parsed)
            capture_admin_configuration(request, refresh=True)
            audit_action(
                request,
                auth,
                f"{resource}.create",
                parsed.id,
                status=201,
                tenant_id=target_tenant,
            )
        return result

    @app.get("/admin/v1/{resource}/{resource_id}", tags=["administration"])
    async def get_resource(resource: str, resource_id: str, request: Request) -> dict[str, Any]:
        auth = admin(request, f"{resource}.get")
        _resource_model(resource)
        result = capture_admin_configuration(request).get_resource(resource, resource_id)
        if result is None:
            raise GatewayError(404, "Resource not found", "invalid_request_error", "resource_not_found")
        target_tenant = result["id"] if resource == "tenants" else result.get("tenant_id")
        audit_action(request, auth, f"{resource}.get", resource_id, tenant_id=target_tenant)
        return result

    @app.put("/admin/v1/{resource}/{resource_id}", tags=["administration"])
    async def replace_resource(resource: str, resource_id: str, request: Request) -> dict[str, Any]:
        auth = admin(request, f"{resource}.replace")
        mutable_config()
        parsed = await _resource_body(resource, request, configured.max_request_bytes)
        if parsed.id != resource_id:
            raise GatewayError(400, "Resource ID cannot change", "invalid_request_error", "resource_id_mismatch")
        target_tenant = parsed.id if resource == "tenants" else getattr(parsed, "tenant_id", None)
        with runtime.store.mutation_transaction():
            capture_admin_configuration(request, refresh=True)
            if runtime.store.get_resource(resource, resource_id) is None:
                raise GatewayError(404, "Resource not found", "invalid_request_error", "resource_not_found")
            validate_resource_links(resource, parsed, runtime.store, configured)
            result = runtime.store.put_resource(resource, parsed)
            capture_admin_configuration(request, refresh=True)
            audit_action(request, auth, f"{resource}.replace", resource_id, tenant_id=target_tenant)
        return result

    @app.delete("/admin/v1/{resource}/{resource_id}", status_code=204, tags=["administration"])
    async def delete_resource(resource: str, resource_id: str, request: Request) -> Response:
        auth = admin(request, f"{resource}.delete")
        mutable_config()
        _resource_model(resource)
        with runtime.store.mutation_transaction():
            capture_admin_configuration(request, refresh=True)
            existing = runtime.store.get_resource(resource, resource_id)
            if existing is None:
                raise GatewayError(404, "Resource not found", "invalid_request_error", "resource_not_found")
            validate_resource_deletion(resource, resource_id, runtime.store)
            deleted = (
                runtime.store.delete_tenant(resource_id)
                if resource == "tenants"
                else runtime.store.delete_resource(resource, resource_id)
            )
            if not deleted:  # defensive against concurrent deletion
                raise GatewayError(404, "Resource not found", "invalid_request_error", "resource_not_found")
            capture_admin_configuration(request, refresh=True)
            target_tenant = existing["id"] if resource == "tenants" else existing.get("tenant_id")
            audit_action(
                request,
                auth,
                f"{resource}.delete",
                resource_id,
                status=204,
                tenant_id=target_tenant,
            )
        return Response(status_code=204)

    return app


def _resource_model(resource: str) -> type[Resource]:
    model = RESOURCE_MODELS.get(resource)
    if model is None:
        raise GatewayError(404, "Unknown resource type", "invalid_request_error", "unknown_resource")
    return model


async def _resource_body(resource: str, request: Request, limit: int) -> Resource:
    model = _resource_model(resource)
    body = await _read_json(request, limit)
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise GatewayError(400, "Resource is invalid", "invalid_request_error", "invalid_resource") from exc


async def _read_json(request: Request, limit: int) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
            if declared_length < 0:
                raise ValueError
            if declared_length > limit:
                raise GatewayError(413, "Request body is too large", "invalid_request_error", "payload_too_large")
        except ValueError as exc:
            raise GatewayError(400, "Content-Length is invalid", "invalid_request_error", "invalid_content_length") from exc
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise GatewayError(413, "Request body is too large", "invalid_request_error", "payload_too_large")
        body.extend(chunk)
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GatewayError(400, "Request body must be valid JSON", "invalid_request_error", "invalid_json") from exc
    if not isinstance(parsed, dict):
        raise GatewayError(400, "Request body must be a JSON object", "invalid_request_error", "invalid_json")
    return parsed
