# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from portable_ai_gateway.errors import GatewayError
from portable_ai_gateway.extensions import (
    ExtensionRequestContext,
    PolicyDecision,
    PolicyEvaluation,
    PolicyExtension,
    RoutingCandidate,
    RoutingExtension,
)
from portable_ai_gateway.providers.base import (
    Endpoint,
    PreparedStream,
    ProviderContext,
    ProviderFailure,
    ProviderResult,
)
from portable_ai_gateway.providers.factory import ProviderFactory
from portable_ai_gateway.schemas import IDENTIFIER_PATTERN, Model, Policy, Quota, Route, RouteTarget
from portable_ai_gateway.store import ConfigurationSnapshot, Store


@dataclass(slots=True)
class RoutingResult:
    result: ProviderResult | PreparedStream
    route_id: str
    provider_id: str
    decisions: list[str]


@dataclass(frozen=True, slots=True)
class RequestedCapabilities:
    """Capability requirements derived once from the admitted request."""

    streaming: bool
    tools: bool
    structured_output: bool
    multimodal: bool


@dataclass(slots=True)
class ResolvedRequest:
    tenant: dict[str, Any]
    model: Model
    route: Route
    policy: Policy | None
    quota: Quota | None
    config_version: str | None
    configuration: ConfigurationSnapshot
    requested_capabilities: RequestedCapabilities


class GatewayRouter:
    """Resolves tenant-owned routes and performs ordered, pre-response fallback."""

    def __init__(
        self,
        store: Store,
        providers: ProviderFactory,
        *,
        policy: PolicyExtension | None = None,
        routing: RoutingExtension | None = None,
        request_deadline_seconds: float | None = None,
    ) -> None:
        if request_deadline_seconds is not None and request_deadline_seconds <= 0:
            raise ValueError("request_deadline_seconds must be positive when configured")
        self.store = store
        self.providers = providers
        self.policy_extension = policy
        self.routing_extension = routing
        self.request_deadline_seconds = request_deadline_seconds

    def resolve(
        self,
        tenant_id: str,
        model_id: str,
        endpoint: Endpoint,
        payload: dict[str, Any],
        *,
        request_id: str = "internal",
        actor: str | None = None,
        configuration: ConfigurationSnapshot | None = None,
    ) -> ResolvedRequest:
        snapshot = configuration or self.store.configuration_snapshot()
        tenant = snapshot.get_resource("tenants", tenant_id)
        if tenant is None or not tenant.get("enabled", False):
            raise GatewayError(403, "Tenant is unavailable", "permission_error", "tenant_unavailable")
        raw_model = snapshot.get_resource("models", model_id)
        if raw_model is None or not raw_model.get("enabled", False) or raw_model.get("tenant_id") not in {None, tenant_id}:
            raise GatewayError(404, "Model is unavailable", "invalid_request_error", "model_not_available")
        model = Model.model_validate(raw_model)
        capability = "chat" if endpoint == "chat/completions" else "responses"
        requested_capabilities = _detect_requested_capabilities(endpoint, payload)
        if not getattr(model.capabilities, capability):
            raise GatewayError(400, f"Model does not support {capability}", "invalid_request_error", "unsupported_capability")
        if requested_capabilities.streaming and not model.capabilities.streaming:
            raise GatewayError(400, "Model does not support streaming", "invalid_request_error", "unsupported_capability")
        if requested_capabilities.tools and not model.capabilities.tools:
            raise GatewayError(400, "Model does not support tools", "invalid_request_error", "unsupported_capability")
        if requested_capabilities.structured_output and not model.capabilities.structured_output:
            raise GatewayError(400, "Model does not support structured output", "invalid_request_error", "unsupported_capability")
        if requested_capabilities.multimodal and not model.capabilities.multimodal:
            raise GatewayError(400, "Model does not support multimodal input", "invalid_request_error", "unsupported_capability")

        raw_routes = [
            item
            for item in snapshot.list_resources("routes", tenant_id)
            if item.get("model_id") == model_id and item.get("enabled", False)
        ]
        if len(raw_routes) != 1:
            code = "route_not_found" if not raw_routes else "ambiguous_route"
            raise GatewayError(503, "Exactly one active model route is required", "server_error", code)
        route = Route.model_validate(raw_routes[0])
        policy = self._single_optional(snapshot, "policies", tenant_id, Policy)
        quota = self._single_optional(snapshot, "quotas", tenant_id, Quota)
        if self.policy_extension is not None:
            self._evaluate_policy(
                request_id,
                tenant_id,
                actor,
                endpoint,
                model_id,
                policy,
                payload,
            )
        elif policy:
            self._enforce_policy(policy, payload)
        return ResolvedRequest(
            tenant,
            model,
            route,
            policy,
            quota,
            snapshot.config_version,
            snapshot,
            requested_capabilities,
        )

    def _evaluate_policy(
        self,
        request_id: str,
        tenant_id: str,
        actor: str | None,
        endpoint: Endpoint,
        model_id: str,
        policy: Policy | None,
        payload: dict[str, Any],
    ) -> None:
        context = ExtensionRequestContext(
            request_id=request_id,
            tenant_id=tenant_id,
            actor=actor,
            operation=endpoint,
            attributes={"model_id": model_id},
        )
        try:
            evaluation = PolicyEvaluation(
                endpoint=endpoint,
                payload=copy.deepcopy(payload),
                configured_policy=policy.model_copy(deep=True) if policy is not None else None,
                serialized_size=len(
                    json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
                ),
                parameter_names=tuple(sorted(payload)),
                stream=payload.get("stream") is True,
            )
            decision = self.policy_extension.evaluate(context, evaluation)
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError(
                503,
                "Policy extension is unavailable",
                "server_error",
                "policy_extension_failed",
            ) from exc
        if (
            not isinstance(decision, PolicyDecision)
            or not isinstance(decision.code, str)
            or re.fullmatch(IDENTIFIER_PATTERN, decision.code) is None
        ):
            raise GatewayError(
                503,
                "Policy extension returned an invalid decision",
                "server_error",
                "policy_extension_invalid",
            )
        if not decision.allowed:
            raise GatewayError(400, "Request denied by policy", "policy_error", decision.code)

    def _single_optional(
        self,
        configuration: ConfigurationSnapshot,
        kind: str,
        tenant_id: str,
        model_type: Any,
    ) -> Any | None:
        values = [
            item
            for item in configuration.list_resources(kind, tenant_id)
            if item.get("enabled", False)
        ]
        if len(values) > 1:
            raise GatewayError(503, f"Multiple active {kind} are configured", "server_error", f"ambiguous_{kind}")
        return model_type.model_validate(values[0]) if values else None

    @staticmethod
    def _enforce_policy(policy: Policy, payload: dict[str, Any]) -> None:
        if policy.max_input_chars is not None:
            size = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
            if size > policy.max_input_chars:
                raise GatewayError(400, "Request exceeds tenant policy", "policy_error", "policy_input_limit")
        denied = sorted(set(policy.denied_parameters).intersection(payload))
        if denied:
            raise GatewayError(400, "Request contains a denied parameter", "policy_error", "policy_parameter_denied")

    async def complete(
        self,
        resolved: ResolvedRequest,
        endpoint: Endpoint,
        payload: dict[str, Any],
        request_id: str,
        tenant_id: str,
        *,
        actor: str | None = None,
    ) -> RoutingResult:
        decisions: list[str] = []
        last_failure: ProviderFailure | None = None
        last_provider: str | None = None
        eligible = self._ordered_eligible_targets(
            resolved,
            endpoint,
            payload,
            request_id,
            tenant_id,
            actor,
            decisions,
        )
        deadline = self._deadline()
        for target, provider in eligible:
            upstream = dict(payload)
            upstream["model"] = target.upstream_model or resolved.model.upstream_model
            try:
                result = await self._await_within_deadline(
                    self.providers.create(provider).complete(
                        endpoint, upstream, ProviderContext(request_id, tenant_id, target.provider_id)
                    ),
                    deadline,
                    target.provider_id,
                )
                decisions.append(f"select:{target.provider_id}")
                return RoutingResult(result, resolved.route.id, target.provider_id, decisions)
            except ProviderFailure as failure:
                decisions.append(f"fail:{target.provider_id}:{failure.code}")
                last_failure = failure
                last_provider = target.provider_id
                if not failure.retryable:
                    break
        self._raise_exhausted(last_failure, decisions, last_provider)

    async def prepare_stream(
        self,
        resolved: ResolvedRequest,
        endpoint: Endpoint,
        payload: dict[str, Any],
        request_id: str,
        tenant_id: str,
        *,
        actor: str | None = None,
    ) -> RoutingResult:
        """All fallback completes before returning the first chunk to the HTTP client."""
        decisions: list[str] = []
        last_failure: ProviderFailure | None = None
        last_provider: str | None = None
        eligible = self._ordered_eligible_targets(
            resolved,
            endpoint,
            payload,
            request_id,
            tenant_id,
            actor,
            decisions,
        )
        deadline = self._deadline()
        for target, provider in eligible:
            upstream = dict(payload)
            upstream["model"] = target.upstream_model or resolved.model.upstream_model
            try:
                result = await self._await_within_deadline(
                    self.providers.create(provider).prepare_stream(
                        endpoint, upstream, ProviderContext(request_id, tenant_id, target.provider_id)
                    ),
                    deadline,
                    target.provider_id,
                )
                decisions.append(f"select:{target.provider_id}")
                return RoutingResult(result, resolved.route.id, target.provider_id, decisions)
            except ProviderFailure as failure:
                decisions.append(f"fail:{target.provider_id}:{failure.code}")
                last_failure = failure
                last_provider = target.provider_id
                if not failure.retryable:
                    break
        self._raise_exhausted(last_failure, decisions, last_provider)

    def _deadline(self) -> float | None:
        if self.request_deadline_seconds is None:
            return None
        return time.monotonic() + self.request_deadline_seconds

    async def _await_within_deadline(
        self,
        awaitable: Any,
        deadline: float | None,
        provider_id: str,
    ) -> Any:
        """Use one monotonic budget across every pre-response provider attempt."""
        if deadline is None:
            return await awaitable
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise self._deadline_exceeded(provider_id)
        try:
            return await asyncio.wait_for(awaitable, timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise self._deadline_exceeded(provider_id) from exc

    @staticmethod
    def _deadline_exceeded(provider_id: str) -> GatewayError:
        return GatewayError(
            504,
            "Gateway request deadline exceeded before a provider response",
            "timeout_error",
            "gateway_deadline_exceeded",
            audit_decision="deadline_exceeded",
            audit_provider=provider_id,
        )

    def _ordered_eligible_targets(
        self,
        resolved: ResolvedRequest,
        endpoint: Endpoint,
        payload: dict[str, Any],
        request_id: str,
        tenant_id: str,
        actor: str | None,
        decisions: list[str],
    ) -> list[tuple[RouteTarget, dict[str, Any]]]:
        eligible: list[tuple[RouteTarget, dict[str, Any]]] = []
        for target in resolved.route.targets:
            provider = self._eligible_provider(
                resolved.configuration,
                target,
                resolved.route,
                tenant_id,
                endpoint,
                resolved.requested_capabilities,
            )
            if provider is None:
                decisions.append(f"skip:{target.provider_id}:ineligible")
            else:
                eligible.append((target, provider))
        if self.routing_extension is None or not eligible:
            return eligible

        candidate_by_provider = {
            target.provider_id: RoutingCandidate(
                provider_id=target.provider_id,
                upstream_model=target.upstream_model or resolved.model.upstream_model,
                priority=target.priority,
                residency=target.residency,
            )
            for target, _ in eligible
        }
        context = ExtensionRequestContext(
            request_id=request_id,
            tenant_id=tenant_id,
            actor=actor,
            operation=endpoint,
            attributes={"model_id": resolved.model.id, "route_id": resolved.route.id},
        )
        try:
            ordered = self.routing_extension.order(context, tuple(candidate_by_provider.values()))
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError(
                503,
                "Routing extension is unavailable",
                "server_error",
                "routing_extension_failed",
            ) from exc
        if not isinstance(ordered, tuple):
            raise GatewayError(
                503,
                "Routing extension returned invalid candidates",
                "server_error",
                "routing_extension_invalid",
            )
        seen: set[str] = set()
        for candidate in ordered:
            if not isinstance(candidate, RoutingCandidate):
                raise GatewayError(
                    503,
                    "Routing extension returned invalid candidates",
                    "server_error",
                    "routing_extension_invalid",
                )
            original = candidate_by_provider.get(candidate.provider_id)
            if candidate != original or candidate.provider_id in seen:
                raise GatewayError(
                    503,
                    "Routing extension returned invalid candidates",
                    "server_error",
                    "routing_extension_invalid",
                )
            seen.add(candidate.provider_id)
        for provider_id in sorted(candidate_by_provider.keys() - seen):
            decisions.append(f"skip:{provider_id}:routing")
        eligible_by_provider = {target.provider_id: (target, provider) for target, provider in eligible}
        return [eligible_by_provider[candidate.provider_id] for candidate in ordered]

    def _eligible_provider(
        self,
        configuration: ConfigurationSnapshot,
        target: RouteTarget,
        route: Route,
        tenant_id: str,
        endpoint: Endpoint,
        requested_capabilities: RequestedCapabilities,
    ) -> dict[str, Any] | None:
        provider = configuration.get_resource("providers", target.provider_id)
        if provider is None or not provider.get("enabled", False):
            return None
        if provider.get("tenant_id") not in {None, tenant_id}:
            return None
        if target.residency != provider.get("residency"):
            return None
        if route.required_residency and (
            target.residency != route.required_residency
            or provider.get("residency") != route.required_residency
        ):
            return None
        if endpoint == "chat/completions" and not target.capabilities.chat:
            return None
        if endpoint == "responses" and (
            not target.capabilities.responses or not provider.get("native_responses", False)
        ):
            return None
        if requested_capabilities.streaming and not target.capabilities.streaming:
            return None
        if requested_capabilities.tools and not target.capabilities.tools:
            return None
        if requested_capabilities.structured_output and not target.capabilities.structured_output:
            return None
        if requested_capabilities.multimodal and not target.capabilities.multimodal:
            return None
        return provider

    @staticmethod
    def _raise_exhausted(
        failure: ProviderFailure | None,
        decisions: list[str],
        last_provider: str | None,
    ) -> None:
        audit_decision = ";".join(decisions)
        if failure is None:
            raise GatewayError(
                503,
                "No eligible provider route",
                "server_error",
                "no_eligible_provider",
                audit_decision=audit_decision,
            )
        raise GatewayError(
            failure.status_code,
            failure.message,
            "upstream_error",
            failure.code,
            audit_decision=audit_decision,
            audit_provider=last_provider,
        )


_TOOL_REQUEST_FIELDS = frozenset(
    {"tools", "tool_choice", "parallel_tool_calls", "functions", "function_call"}
)
_TOOL_INTERACTION_FIELDS = frozenset({"tool_calls", "tool_call_id", "function_call"})
_TOOL_INTERACTION_TYPES = frozenset(
    {"function_call", "function_call_output", "tool_call", "tool_result"}
)
_MULTIMODAL_TYPES = frozenset(
    {
        "image",
        "image_url",
        "image_file",
        "input_image",
        "output_image",
        "computer_screenshot",
        "audio",
        "audio_url",
        "input_audio",
        "output_audio",
        "video",
        "video_url",
        "input_video",
        "output_video",
        "file",
        "file_url",
        "input_file",
        "output_file",
    }
)
_MULTIMODAL_FIELDS = frozenset(
    {
        "image",
        "image_url",
        "image_data",
        "image_file",
        "input_image",
        "output_image",
        "audio",
        "audio_url",
        "audio_data",
        "input_audio",
        "output_audio",
        "video",
        "video_url",
        "video_data",
        "input_video",
        "output_video",
        "file",
        "file_id",
        "file_url",
        "file_data",
        "filename",
        "input_file",
        "output_file",
    }
)


def _detect_requested_capabilities(
    endpoint: Endpoint,
    payload: dict[str, Any],
) -> RequestedCapabilities:
    interaction_roots = tuple(
        payload[field]
        for field in ("messages", "input")
        if field in payload
    )
    tools = any(field in payload for field in _TOOL_REQUEST_FIELDS) or any(
        _contains_tool_interaction(value) for value in interaction_roots
    )
    structured_output = "response_format" in payload or (
        endpoint == "responses"
        and isinstance(payload.get("text"), dict)
        and "format" in payload["text"]
    )
    multimodal = _has_non_text_modality(payload.get("modalities")) or any(
        field in payload for field in _MULTIMODAL_FIELDS
    ) or any(_requires_multimodal(value) for value in interaction_roots)
    return RequestedCapabilities(
        streaming=payload.get("stream") is True,
        tools=tools,
        structured_output=structured_output,
        multimodal=multimodal,
    )


def _contains_tool_interaction(value: Any) -> bool:
    if isinstance(value, dict):
        role = value.get("role")
        interaction_type = value.get("type")
        if isinstance(role, str) and role.lower() == "tool":
            return True
        if any(field in value for field in _TOOL_INTERACTION_FIELDS):
            return True
        if isinstance(interaction_type, str) and interaction_type.lower() in _TOOL_INTERACTION_TYPES:
            return True
        return any(_contains_tool_interaction(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_tool_interaction(item) for item in value)
    return False


def _has_non_text_modality(value: Any) -> bool:
    if value is None:
        return False
    modalities = value if isinstance(value, list) else [value]
    return any(
        not isinstance(modality, str) or modality.lower() != "text"
        for modality in modalities
    )


def _requires_multimodal(value: Any) -> bool:
    if isinstance(value, dict):
        content_type = value.get("type")
        if isinstance(content_type, str) and content_type.lower() in _MULTIMODAL_TYPES:
            return True
        if any(field in value for field in _MULTIMODAL_FIELDS):
            return True
        return any(_requires_multimodal(item) for item in value.values())
    if isinstance(value, list):
        return any(_requires_multimodal(item) for item in value)
    return False
