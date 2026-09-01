# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import ipaddress
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from portable_ai_gateway.cache import QuotaEnforcer
from portable_ai_gateway.config import Settings
from portable_ai_gateway.errors import GatewayError
from portable_ai_gateway.extensions import AuditSink
from portable_ai_gateway.provider_endpoints import vertex_base_url
from portable_ai_gateway.providers.base import Endpoint, PreparedStream, ProviderResult
from portable_ai_gateway.routing import GatewayRouter, ResolvedRequest, RoutingResult
from portable_ai_gateway.schemas import AuditEvent, Model, Provider, RESOURCE_MODELS, Resource, Route
from portable_ai_gateway.security import AuthContext
from portable_ai_gateway.store import Store, utc_now


@dataclass(slots=True)
class InferenceOutcome:
    result: ProviderResult | PreparedStream
    provider_id: str
    route_id: str
    release: Callable[[], Awaitable[None]] | None = None
    finalize_stream: Callable[[int, str | None], None] | None = None
    ensure_lease_active: Callable[[], None] | None = None


class AuditRecorder:
    def __init__(self, store: Store, config_version: str, sink: AuditSink | None = None) -> None:
        self._store = store
        self._fallback_config_version = config_version
        self._sink = sink

    def record(
        self,
        *,
        request_id: str,
        auth: AuthContext,
        action: str,
        status: int,
        model: str | None = None,
        route: str | None = None,
        provider: str | None = None,
        decision: str | None = None,
        resource: str | None = None,
        error_code: str | None = None,
        tenant_id: str | None = None,
        config_version: str | None = None,
    ) -> None:
        event = AuditEvent(
            id=str(uuid.uuid4()),
            timestamp=utc_now(),
            request_id=request_id,
            actor=auth.actor,
            tenant_id=auth.tenant_id if tenant_id is None else tenant_id,
            action=action,
            resource=resource,
            model=model,
            route=route,
            decision=decision,
            provider=provider,
            status=status,
            config_version=(
                config_version
                if config_version is not None
                else self._store.get_config_version() or self._fallback_config_version
            ),
            error_code=error_code,
        )
        if self._sink is None:
            self._store.append_audit(event)
        else:
            self._sink.emit(event)

    def effective_config_version(self, captured: str | None) -> str:
        """Resolve an already captured revision without consulting mutable state."""
        return captured or self._fallback_config_version


class InferenceService:
    def __init__(self, store: Store, router: GatewayRouter, quotas: QuotaEnforcer, audit: AuditRecorder) -> None:
        self.store = store
        self.router = router
        self.quotas = quotas
        self.audit = audit

    async def infer(
        self,
        endpoint: Endpoint,
        payload: dict[str, Any],
        auth: AuthContext,
        request_id: str,
    ) -> InferenceOutcome:
        action = "responses.create" if endpoint == "responses" else "chat.completions.create"
        candidate_model = payload.get("model")
        model_id = candidate_model if isinstance(candidate_model, str) and candidate_model else None
        resolved: ResolvedRequest | None = None
        configuration = None
        config_version = self.audit.effective_config_version(None)
        lease: Any = None
        lease_guard: Any = None
        lease_entered = False
        phase = "authorization"
        try:
            configuration = self.store.configuration_snapshot()
            config_version = self.audit.effective_config_version(configuration.config_version)
            auth.require("inference:write")
            if auth.tenant_id is None:
                raise GatewayError(403, "A tenant claim is required", "permission_error", "tenant_required")
            tenant = configuration.get_resource("tenants", auth.tenant_id)
            if tenant is None or not tenant.get("enabled", False):
                raise GatewayError(403, "Tenant is unavailable", "permission_error", "tenant_unavailable")
            if tenant.get("sensitive", False) and auth.method == "api-key":
                raise GatewayError(403, "Sensitive tenants require OIDC", "permission_error", "oidc_required")
            if model_id is None:
                raise GatewayError(400, "model is required", "invalid_request_error", "missing_model")
            phase = "routing"
            resolved = self.router.resolve(
                auth.tenant_id,
                model_id,
                endpoint,
                payload,
                request_id=request_id,
                actor=auth.actor,
                configuration=configuration,
            )
            config_version = self.audit.effective_config_version(resolved.config_version)
            rpm = resolved.quota.requests_per_minute if resolved.quota else None
            concurrent = resolved.quota.concurrent_requests if resolved.quota else None
            phase = "quota"
            await self.quotas.check_rate(auth.tenant_id, rpm)
            lease = self.quotas.concurrent(auth.tenant_id, concurrent)
            lease_guard = await lease.__aenter__()
            lease_entered = True
            phase = "provider"
            if payload.get("stream") is True:
                routed = await self.router.prepare_stream(
                    resolved,
                    endpoint,
                    payload,
                    request_id,
                    auth.tenant_id,
                    actor=auth.actor,
                )
                released = False

                async def release() -> None:
                    nonlocal released
                    if not released:
                        released = True
                        await lease.__aexit__(None, None, None)

                def finalize_stream(status: int, error_code: str | None) -> None:
                    self.audit.record(
                        request_id=request_id,
                        auth=auth,
                        action=action,
                        status=status,
                        model=model_id,
                        route=routed.route_id,
                        provider=routed.provider_id,
                        decision=";".join(routed.decisions),
                        error_code=error_code,
                        config_version=config_version,
                    )

                def ensure_lease_active() -> None:
                    if lease_guard is not None:
                        lease_guard.ensure_active()

                return InferenceOutcome(
                    routed.result,
                    routed.provider_id,
                    routed.route_id,
                    release,
                    finalize_stream,
                    ensure_lease_active,
                )
            routed = await self.router.complete(
                resolved,
                endpoint,
                payload,
                request_id,
                auth.tenant_id,
                actor=auth.actor,
            )
            if lease_guard is not None:
                lease_guard.ensure_active()
            phase = "audit"
            self._audit_success(
                request_id,
                auth,
                action,
                model_id,
                routed,
                config_version,
            )
            await lease.__aexit__(None, None, None)
            return InferenceOutcome(routed.result, routed.provider_id, routed.route_id)
        except GatewayError as exc:
            if lease is not None and lease_entered:
                await lease.__aexit__(type(exc), exc, exc.__traceback__)
            try:
                self.audit.record(
                    request_id=request_id,
                    auth=auth,
                    action=action,
                    status=exc.status_code,
                    model=model_id,
                    route=resolved.route.id if resolved else None,
                    provider=exc.audit_provider,
                    decision=exc.audit_decision or (
                        "deny" if exc.error_type in {"permission_error", "policy_error"} else "error"
                    ),
                    error_code=exc.code,
                    config_version=config_version,
                )
            except Exception:
                pass
            raise
        except asyncio.CancelledError as exc:
            if lease is not None and lease_entered:
                await lease.__aexit__(type(exc), exc, exc.__traceback__)
            try:
                self.audit.record(
                    request_id=request_id,
                    auth=auth,
                    action=action,
                    status=499,
                    model=model_id,
                    route=resolved.route.id if resolved else None,
                    decision="cancelled",
                    error_code="request_cancelled",
                    config_version=config_version,
                )
            except Exception:
                # Cancellation must still propagate even if its best-effort
                # terminal audit record cannot be persisted.
                pass
            raise
        except Exception as exc:
            if lease is not None and lease_entered:
                await lease.__aexit__(type(exc), exc, exc.__traceback__)
            dependency_failure = phase in {"authorization", "routing", "quota", "audit"}
            status = 503 if dependency_failure else 502
            code = "quota_backend_unavailable" if phase == "quota" else (
                "gateway_dependency_unavailable" if dependency_failure else "provider_failure"
            )
            try:
                self.audit.record(
                    request_id=request_id,
                    auth=auth,
                    action=action,
                    status=status,
                    model=model_id,
                    route=resolved.route.id if resolved else None,
                    decision="error",
                    error_code=code,
                    config_version=config_version,
                )
            except Exception:
                pass
            message = "Gateway dependency is unavailable" if dependency_failure else "Provider execution failed"
            error_type = "server_error" if dependency_failure else "upstream_error"
            raise GatewayError(status, message, error_type, code) from exc

    def _audit_success(
        self,
        request_id: str,
        auth: AuthContext,
        action: str,
        model_id: str,
        routed: RoutingResult,
        config_version: str,
    ) -> None:
        self.audit.record(
            request_id=request_id,
            auth=auth,
            action=action,
            status=routed.result.status_code,
            model=model_id,
            route=routed.route_id,
            provider=routed.provider_id,
            decision=";".join(routed.decisions),
            config_version=config_version,
        )


def validate_chat_payload(payload: dict[str, Any]) -> None:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise GatewayError(400, "messages must be a non-empty array", "invalid_request_error", "invalid_messages")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise GatewayError(400, "messages contains an invalid role", "invalid_request_error", "invalid_messages")
        if message["role"] == "tool" and not isinstance(message.get("tool_call_id"), str):
            raise GatewayError(400, "tool messages require tool_call_id", "invalid_request_error", "invalid_messages")


def validate_provider_network(provider: Provider, settings: Settings) -> None:
    """Prevents arbitrary admin input from turning the gateway into an SSRF primitive."""
    base_url = provider.base_url
    if provider.kind == "vertex" and base_url is None:
        # The default Vertex endpoint is validated exactly like an explicitly
        # configured endpoint; otherwise location would become an unchecked host input.
        base_url = vertex_base_url(provider.project or "", provider.location or "")
    if not base_url:
        return
    hostname = (urlparse(base_url).hostname or "").lower()
    if not hostname:
        raise GatewayError(400, "Provider URL has no hostname", "invalid_request_error", "invalid_provider_url")
    allowed = hostname in settings.provider_host_allowlist
    if settings.app_env == "production" and not allowed:
        raise GatewayError(
            400,
            "Provider hostname is not in PAG_PROVIDER_HOST_ALLOWLIST",
            "invalid_request_error",
            "provider_host_not_allowed",
        )
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    unsafe = hostname == "localhost" or hostname.endswith(".localhost")
    if address is not None:
        unsafe = unsafe or address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
    if unsafe and settings.app_env != "development" and not allowed:
        raise GatewayError(400, "Private provider hostname is not allowed", "invalid_request_error", "provider_host_not_allowed")


def validate_resource_links(kind: str, resource: Resource, store: Store, settings: Settings) -> None:
    if kind not in RESOURCE_MODELS:
        raise GatewayError(404, "Unknown resource type", "invalid_request_error", "unknown_resource")
    tenant_id = getattr(resource, "tenant_id", None)
    if tenant_id is not None and store.get_resource("tenants", tenant_id) is None:
        raise GatewayError(400, "Referenced tenant does not exist", "invalid_request_error", "invalid_tenant")
    if kind == "providers":
        validated_provider = Provider.model_validate(resource)
        validate_provider_network(validated_provider, settings)
        for raw_route in store.list_resources("routes"):
            if any(
                target.get("provider_id") == validated_provider.id
                for target in raw_route.get("targets", [])
            ):
                raw_model = store.get_resource("models", raw_route["model_id"])
                if raw_model is not None:
                    _validate_route_contract(
                        Route.model_validate(raw_route),
                        Model.model_validate(raw_model),
                        store,
                        provider_override=validated_provider,
                    )
    if kind == "models":
        validated_model = Model.model_validate(resource)
        for raw_route in store.list_resources("routes"):
            if raw_route.get("model_id") == validated_model.id:
                _validate_route_contract(
                    Route.model_validate(raw_route),
                    validated_model,
                    store,
                )
    if kind in {"routes", "policies", "quotas"} and tenant_id is None:
        raise GatewayError(400, f"{kind} must be tenant-owned", "invalid_request_error", "tenant_required")
    if kind in {"policies", "quotas"} and resource.enabled:
        conflicts = [
            item
            for item in store.list_resources(kind, tenant_id)
            if item.get("enabled", False) and item.get("id") != resource.id
        ]
        if conflicts:
            raise GatewayError(
                409,
                f"Only one active {kind} resource is allowed per tenant",
                "conflict_error",
                f"ambiguous_{kind}",
            )
    if kind == "routes":
        route = Route.model_validate(resource)
        raw_model = store.get_resource("models", route.model_id)
        if raw_model is None or raw_model.get("tenant_id") not in {None, route.tenant_id}:
            raise GatewayError(400, "Route model is not owned by the tenant", "invalid_request_error", "invalid_model")
        _validate_route_contract(route, Model.model_validate(raw_model), store)
        if route.enabled:
            conflicts = [
                item
                for item in store.list_resources("routes", route.tenant_id)
                if item.get("enabled", False)
                and item.get("model_id") == route.model_id
                and item.get("id") != route.id
            ]
            if conflicts:
                raise GatewayError(
                    409,
                    "Only one active route is allowed per tenant and model",
                    "conflict_error",
                    "ambiguous_route",
                )


def _validate_route_contract(
    route: Route,
    model: Model,
    store: Store,
    *,
    provider_override: Provider | None = None,
) -> None:
    """Ensure at least one target can honor the complete advertised model contract."""
    capability_names = (
        "chat",
        "responses",
        "streaming",
        "tools",
        "structured_output",
        "multimodal",
    )
    fully_compatible = False
    native_response_capable = not model.capabilities.responses
    for target in route.targets:
        if provider_override is not None and provider_override.id == target.provider_id:
            provider = provider_override.model_dump(mode="json")
        else:
            provider = store.get_resource("providers", target.provider_id)
        if provider is None or provider.get("tenant_id") not in {None, route.tenant_id}:
            raise GatewayError(
                400,
                "Route provider is not owned by the tenant",
                "invalid_request_error",
                "invalid_provider",
            )
        if target.residency != provider.get("residency"):
            raise GatewayError(
                400,
                "Route target residency must equal its provider attestation",
                "invalid_request_error",
                "invalid_residency",
            )
        if route.required_residency and target.residency != route.required_residency:
            continue
        if target.capabilities.responses and provider.get("native_responses", False):
            native_response_capable = True
        target_supports = all(
            not getattr(model.capabilities, name) or getattr(target.capabilities, name)
            for name in capability_names
        )
        if model.capabilities.responses and not provider.get("native_responses", False):
            target_supports = False
        fully_compatible = fully_compatible or target_supports
    if not native_response_capable:
        raise GatewayError(
            400,
            "Responses-enabled models require a native Responses provider",
            "invalid_request_error",
            "native_responses_required",
        )
    if not fully_compatible:
        raise GatewayError(
            400,
            "No route target satisfies the model capability and residency contract",
            "invalid_request_error",
            "incompatible_route_target",
        )


def validate_resource_deletion(kind: str, resource_id: str, store: Store) -> None:
    """Reject deletions that would leave active routes with dangling references."""
    if kind not in {"providers", "models"}:
        return
    for route in store.list_resources("routes"):
        if not route.get("enabled", False):
            continue
        referenced = (
            route.get("model_id") == resource_id
            if kind == "models"
            else any(target.get("provider_id") == resource_id for target in route.get("targets", []))
        )
        if referenced:
            raise GatewayError(
                409,
                f"{kind[:-1].capitalize()} is referenced by an active route",
                "conflict_error",
                "resource_in_use",
            )
