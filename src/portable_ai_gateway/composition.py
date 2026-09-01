# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from portable_ai_gateway.cache import Cache, QuotaEnforcer
from portable_ai_gateway.config import Settings
from portable_ai_gateway.extensions import (
    AUDIT_SPI_VERSION,
    AUTH_SPI_VERSION,
    OBSERVABILITY_SPI_VERSION,
    POLICY_SPI_VERSION,
    PROVIDER_SPI_VERSION,
    ROUTING_SPI_VERSION,
    ApiKeyLifecycle,
    AuditSink,
    AuthenticationExtension,
    ExtensionRequestContext,
    ObservabilityHook,
    PolicyDecision,
    PolicyEvaluation,
    PolicyExtension,
    ProviderAdapter,
    RoutingCandidate,
    RoutingExtension,
    validate_extension,
)
from portable_ai_gateway.providers.base import (
    Endpoint,
    PreparedStream,
    ProviderContext,
    ProviderFailure,
    ProviderResult,
)
from portable_ai_gateway.providers.factory import ProviderFactory
from portable_ai_gateway.routing import GatewayRouter
from portable_ai_gateway.schemas import AuditEvent
from portable_ai_gateway.security import AuthContext, AuthService
from portable_ai_gateway.service import AuditRecorder, InferenceService
from portable_ai_gateway.store import Store


LOGGER = logging.getLogger("portable_ai_gateway.extensions")


class FirstPartyAuthenticationExtension:
    """Expose the built-in OIDC/API-key service through the public auth SPI."""

    spi_version = AUTH_SPI_VERSION

    def __init__(self, settings: Settings, store: Store) -> None:
        self._delegate = AuthService(settings, store)
        self.api_keys = self._delegate.api_keys

    def authenticate(self, authorization: str | None) -> AuthContext:
        return self._delegate.authenticate(authorization)


class FirstPartyPolicyExtension:
    """Apply the built-in request-side tenant policy through ``pag.policy/v1``."""

    spi_version = POLICY_SPI_VERSION

    def evaluate(
        self,
        context: ExtensionRequestContext,
        evaluation: PolicyEvaluation,
    ) -> PolicyDecision:
        del context
        policy = evaluation.configured_policy
        if policy is None:
            return PolicyDecision(True, "allowed")
        if (
            policy.max_input_chars is not None
            and evaluation.serialized_size > policy.max_input_chars
        ):
            return PolicyDecision(False, "policy_input_limit")
        if set(policy.denied_parameters).intersection(evaluation.parameter_names):
            return PolicyDecision(False, "policy_parameter_denied")
        return PolicyDecision(True, "allowed")


class FirstPartyRoutingExtension:
    """Preserve the configured priority order for already eligible targets."""

    spi_version = ROUTING_SPI_VERSION

    def order(
        self,
        context: ExtensionRequestContext,
        candidates: tuple[RoutingCandidate, ...],
    ) -> tuple[RoutingCandidate, ...]:
        del context
        return tuple(sorted(candidates, key=lambda candidate: candidate.priority))


class StoreAuditSink:
    """Persist typed audit events in the configured source-of-truth store."""

    spi_version = AUDIT_SPI_VERSION

    def __init__(self, store: Store) -> None:
        self._store = store

    def emit(self, event: AuditEvent) -> None:
        self._store.append_audit(event)


class NoopObservabilityHook:
    """Explicit first-party hook; structured request logging remains in the app."""

    spi_version = OBSERVABILITY_SPI_VERSION

    async def request_finished(
        self,
        context: ExtensionRequestContext,
        status_code: int,
        duration_ms: float,
    ) -> None:
        del context, status_code, duration_ms

    async def provider_finished(
        self,
        context: ExtensionRequestContext,
        provider_id: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        del context, provider_id, status_code, duration_ms


class ObservedProviderAdapter:
    """Adapter that consumes the provider and observability SPIs together."""

    spi_version = PROVIDER_SPI_VERSION

    def __init__(self, delegate: ProviderAdapter, observability: ObservabilityHook) -> None:
        self._delegate = delegate
        self._observability = observability

    async def complete(
        self,
        endpoint: Endpoint,
        payload: dict[str, Any],
        context: ProviderContext,
    ) -> ProviderResult:
        started = time.monotonic()
        status_code = 502
        try:
            result = await self._delegate.complete(endpoint, payload, context)
            status_code = result.status_code
            return result
        except ProviderFailure as exc:
            status_code = exc.status_code
            raise
        finally:
            await self._provider_finished(endpoint, context, status_code, started)

    async def prepare_stream(
        self,
        endpoint: Endpoint,
        payload: dict[str, Any],
        context: ProviderContext,
    ) -> PreparedStream:
        started = time.monotonic()
        status_code = 502
        try:
            result = await self._delegate.prepare_stream(endpoint, payload, context)
            status_code = result.status_code
            return result
        except ProviderFailure as exc:
            status_code = exc.status_code
            raise
        finally:
            await self._provider_finished(endpoint, context, status_code, started)

    async def _provider_finished(
        self,
        endpoint: Endpoint,
        context: ProviderContext,
        status_code: int,
        started: float,
    ) -> None:
        extension_context = ExtensionRequestContext(
            request_id=context.request_id,
            tenant_id=context.tenant_id,
            actor=None,
            operation=endpoint,
            attributes={"provider_id": context.provider_id},
        )
        try:
            await self._observability.provider_finished(
                extension_context,
                context.provider_id,
                status_code,
                (time.monotonic() - started) * 1000,
            )
        except Exception:
            LOGGER.warning("provider_observability_hook_failed", exc_info=True)


class ObservedProviderFactory:
    """Validate every customer/provider adapter before adding telemetry."""

    def __init__(self, delegate: Any, observability: ObservabilityHook) -> None:
        self._delegate = delegate
        self._observability = observability

    def create(self, provider: dict[str, Any]) -> ProviderAdapter:
        adapter = self._delegate.create(provider)
        validate_extension(adapter, ProviderAdapter, PROVIDER_SPI_VERSION)
        return ObservedProviderAdapter(adapter, self._observability)


class GatewayComposition:
    """Versioned build-time composition seam for every first-party adapter family.

    Customer builds may subclass this object or provide a duck-compatible object.
    The domain and public schemas remain unchanged; no package is downloaded or
    imported dynamically at runtime.
    """

    spi_version = "pag.composition/v1"

    def create_authentication_extension(
        self,
        settings: Settings,
        store: Store,
    ) -> AuthenticationExtension:
        return FirstPartyAuthenticationExtension(settings, store)

    def create_policy_extension(self) -> PolicyExtension:
        return FirstPartyPolicyExtension()

    def create_routing_extension(self) -> RoutingExtension:
        return FirstPartyRoutingExtension()

    def create_audit_sink(self, store: Store) -> AuditSink:
        return StoreAuditSink(store)

    def create_observability_hook(self) -> ObservabilityHook:
        return NoopObservabilityHook()

    def create_authentication(self, settings: Settings, store: Store) -> Any:
        extension = self.create_authentication_extension(settings, store)
        validate_extension(extension, AuthenticationExtension, AUTH_SPI_VERSION)
        if not isinstance(extension.api_keys, ApiKeyLifecycle):
            raise TypeError("Authentication extension does not implement ApiKeyLifecycle")
        return extension

    def create_audit(self, store: Store, config_version: str) -> Any:
        sink = self.create_audit_sink(store)
        validate_extension(sink, AuditSink, AUDIT_SPI_VERSION)
        return AuditRecorder(store, config_version, sink=sink)

    def create_observability(self) -> ObservabilityHook:
        extension = self.create_observability_hook()
        validate_extension(extension, ObservabilityHook, OBSERVABILITY_SPI_VERSION)
        return extension

    def create_provider_factory(self, client: httpx.AsyncClient) -> Any:
        return ProviderFactory(client)

    def observe_provider_factory(
        self,
        providers: Any,
        observability: ObservabilityHook,
    ) -> Any:
        return ObservedProviderFactory(providers, observability)

    def create_router(self, store: Store, providers: Any) -> Any:
        policy = self.create_policy_extension()
        routing = self.create_routing_extension()
        validate_extension(policy, PolicyExtension, POLICY_SPI_VERSION)
        validate_extension(routing, RoutingExtension, ROUTING_SPI_VERSION)
        return GatewayRouter(store, providers, policy=policy, routing=routing)

    def create_inference(
        self,
        store: Store,
        router: Any,
        cache: Cache,
        audit: Any,
    ) -> Any:
        return InferenceService(store, router, QuotaEnforcer(cache), audit)

    def configure_app(self, app: Any, runtime: Any) -> None:
        """Install reviewed policy/observability hooks in a customer build."""
        return None
