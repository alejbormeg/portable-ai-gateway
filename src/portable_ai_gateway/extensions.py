# SPDX-License-Identifier: AGPL-3.0-only
"""Stable v1 extension contracts.

Implementations are ordinary Python objects supplied by the customer build. The alpha
does not perform dynamic package downloads or remote plugin loading.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from portable_ai_gateway.providers.base import Endpoint, ProviderAdapter
from portable_ai_gateway.schemas import ApiKeyCreate, AuditEvent, Policy
from portable_ai_gateway.security import AuthContext


AUTH_SPI_VERSION = "pag.auth/v1"
POLICY_SPI_VERSION = "pag.policy/v1"
ROUTING_SPI_VERSION = "pag.routing/v1"
AUDIT_SPI_VERSION = "pag.audit/v1"
OBSERVABILITY_SPI_VERSION = "pag.observability/v1"
PROVIDER_SPI_VERSION = "pag.provider/v1"


@dataclass(frozen=True, slots=True)
class ExtensionRequestContext:
    request_id: str
    tenant_id: str | None
    actor: str | None
    operation: str
    attributes: dict[str, str]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    code: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """Request-side guardrail input; ``payload`` is a disposable defensive copy."""

    endpoint: Endpoint
    payload: dict[str, Any]
    configured_policy: Policy | None
    serialized_size: int
    parameter_names: tuple[str, ...]
    stream: bool


@dataclass(frozen=True, slots=True)
class RoutingCandidate:
    provider_id: str
    upstream_model: str
    priority: int
    residency: str | None = None


@runtime_checkable
class ApiKeyLifecycle(Protocol):
    """Runtime operations needed by the control plane for scoped API keys."""

    def issue(self, request: ApiKeyCreate) -> tuple[dict[str, Any], str]: ...

    def rotate(self, key_id: str) -> tuple[dict[str, Any], str]: ...

    def revoke(self, key_id: str) -> dict[str, Any]: ...


@runtime_checkable
class AuthenticationExtension(Protocol):
    """Authenticate the complete HTTP Authorization value and manage API keys."""

    spi_version: str

    @property
    def api_keys(self) -> ApiKeyLifecycle: ...

    def authenticate(self, authorization: str | None) -> AuthContext: ...


@runtime_checkable
class PolicyExtension(Protocol):
    """Inspect one request-side guardrail input and return a fail-closed decision."""

    spi_version: str

    def evaluate(
        self,
        context: ExtensionRequestContext,
        evaluation: PolicyEvaluation,
    ) -> PolicyDecision: ...


@runtime_checkable
class RoutingExtension(Protocol):
    """Order or remove already eligible routing candidates."""

    spi_version: str

    def order(
        self, context: ExtensionRequestContext, candidates: tuple[RoutingCandidate, ...]
    ) -> tuple[RoutingCandidate, ...]: ...


@runtime_checkable
class AuditSink(Protocol):
    """Persist one validated, metadata-only event before ``emit`` returns."""

    spi_version: str

    def emit(self, event: AuditEvent) -> None: ...


@runtime_checkable
class ObservabilityHook(Protocol):
    """Best-effort asynchronous operational telemetry callbacks."""

    spi_version: str

    async def request_finished(
        self, context: ExtensionRequestContext, status_code: int, duration_ms: float
    ) -> None: ...

    async def provider_finished(
        self, context: ExtensionRequestContext, provider_id: str, status_code: int, duration_ms: float
    ) -> None: ...


def validate_extension(instance: Any, protocol: type[Any], expected_version: str) -> None:
    if not isinstance(instance, protocol):
        raise TypeError(f"Extension does not implement {protocol.__name__}")
    if getattr(instance, "spi_version", None) != expected_version:
        raise ValueError(f"Extension requires {expected_version}")


__all__ = [
    "AUDIT_SPI_VERSION",
    "AUTH_SPI_VERSION",
    "OBSERVABILITY_SPI_VERSION",
    "POLICY_SPI_VERSION",
    "PROVIDER_SPI_VERSION",
    "ROUTING_SPI_VERSION",
    "ApiKeyLifecycle",
    "AuditSink",
    "AuthenticationExtension",
    "ExtensionRequestContext",
    "ObservabilityHook",
    "PolicyDecision",
    "PolicyEvaluation",
    "PolicyExtension",
    "ProviderAdapter",
    "RoutingCandidate",
    "RoutingExtension",
    "validate_extension",
]
