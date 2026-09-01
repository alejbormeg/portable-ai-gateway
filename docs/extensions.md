<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Extension contracts

Portable AI Gateway exposes Python Protocols and neutral DTOs so a customer fork can replace provider, authentication, policy/guardrail, routing, audit, and observability behavior without importing HTTP framework objects into the extension. All six public contracts are exported by `portable_ai_gateway.extensions`; `ProviderAdapter` is defined in `portable_ai_gateway.providers.base` and re-exported from that public module.

The alpha uses **build-time composition**. `GatewayComposition` (`pag.composition/v1`) is the explicit injection seam accepted by both application factories. A customer subclasses it in their image build and overrides the six low-level factory methods below; no domain schema or inference-service edit is required. There is no marketplace, remote plugin service, dynamic package download, arbitrary path import, or runtime execution of untrusted code.

## Contract versions

| Capability | Identifier | Protocol |
| --- | --- | --- |
| Composition root | `pag.composition/v1` | `GatewayComposition` |
| Provider | `pag.provider/v1` | `ProviderAdapter` |
| Authentication | `pag.auth/v1` | `AuthenticationExtension` |
| Policy | `pag.policy/v1` | `PolicyExtension` |
| Routing | `pag.routing/v1` | `RoutingExtension` |
| Audit | `pag.audit/v1` | `AuditSink` |
| Observability | `pag.observability/v1` | `ObservabilityHook` |

Every extension object declares `spi_version`. `validate_extension(instance, protocol, expected_version)` checks the structural contract and exact version. Authentication, policy, routing, audit, and observability are validated while the application is composed. Every enabled provider is instantiated and validated at startup, and every adapter returned later is checked again. A mismatch fails startup; it never falls back to an unvalidated implementation.

## Composition points and first-party adapters

| Family | Override in `GatewayComposition` | First-party implementation/runtime adapter |
| --- | --- | --- |
| Provider | `create_provider_factory(client)` | `ProviderFactory`; each adapter is wrapped by `ObservedProviderAdapter` |
| Authentication | `create_authentication_extension(settings, store)` | `FirstPartyAuthenticationExtension` |
| Policy/guardrail | `create_policy_extension()` | `FirstPartyPolicyExtension`, consumed by `GatewayRouter.resolve` |
| Routing | `create_routing_extension()` | `FirstPartyRoutingExtension`, consumed before every provider attempt |
| Audit | `create_audit_sink(store)` | `StoreAuditSink`, consumed synchronously by `AuditRecorder` |
| Observability | `create_observability_hook()` | `NoopObservabilityHook`, consumed by HTTP middleware and provider wrappers |

The higher-level methods `create_authentication`, `create_audit`, `create_router`, `create_inference`, `observe_provider_factory`, and `configure_app` remain available for compatibility and advanced forks. Overriding a low-level factory is the supported path because the base composition keeps contract validation and the safety adapters in place.

## Provider

`ProviderAdapter.complete(endpoint, payload, context)` and `prepare_stream(...)` are asynchronous. `complete` returns a `ProviderResult`; `prepare_stream` must validate/obtain a first chunk and return `PreparedStream` so routing can finish all fallback before the HTTP response begins. Provider exceptions use `ProviderFailure` with a safe message, machine code, retryability, and status. Adapters must not return credentials/upstream bodies in errors or silently emulate unsupported capabilities.

`ProviderContext` includes request, tenant, and provider IDs only. The factory implementations for mock, generic OpenAI-compatible, and Vertex are examples; a customer build may replace or extend the factory without changing public HTTP/domain schemas.

## Authentication

`AuthenticationExtension.authenticate(authorization)` is synchronous and receives the complete `Authorization` header value, or `None`. It returns the common `AuthContext`: actor, optional tenant, scopes, and authentication method. The extension also exposes an `api_keys` object implementing `issue`, `rotate`, and `revoke`, because the control plane consumes those operations. An extension must verify signature/issuer/audience/time/revocation as applicable, derive tenant server-side, and fail closed with content-free errors. It must not log or propagate token bytes.

## Policy

`PolicyExtension.evaluate(context, evaluation)` is synchronous and runs for every inference after tenant/model capability resolution and before quota/provider execution. `evaluation` is a typed `PolicyEvaluation` containing the endpoint, a deep defensive copy of the validated request payload, the configured tenant `Policy`, serialized size, sorted parameter names, and the stream flag. A customer guardrail can therefore inspect `messages` or `input`; mutations affect only the disposable copy and never rewrite the request sent to the provider.

The payload can contain prompts, user input, tool arguments, and other sensitive customer content. A policy extension must process it in memory, minimize retention, and must not persist it, log it, place it in errors/metrics/traces, or transmit it to another service unless the operator has explicitly designed and authorized that data flow. The supplied metadata-only context and audit contracts do not make content safe to retain.

The return value is `PolicyDecision(allowed, code, reason)`. A denial code must match the public identifier grammar; invalid output or an exception fails closed. `pag.policy/v1` is a **request-side guardrail contract only**. It makes no claim to inspect, transform, or block provider output or streamed response content.

## Routing

`RoutingExtension.order(context, candidates)` is synchronous and receives only targets that already passed tenant, capability, provider, and residency checks. It returns a tuple that may reorder or remove candidates. Runtime validation requires an unchanged, duplicate-free subset; adding a provider or changing upstream model, priority, or residency fails closed. It cannot enable post-first-byte replay because it is invoked before a provider call and streaming fallback still ends before the first downstream byte.

## Audit and observability

`AuditSink.emit(AuditEvent)` is synchronous: it must persist, buffer durably, or raise before returning. It receives the typed metadata-only event; the DTO has no prompt/response fields. Audit sink failure therefore follows the existing fail-closed/best-effort behavior of the call site instead of becoming an untracked background task.

`ObservabilityHook.request_finished` and `provider_finished` are asynchronous, best-effort callbacks. The HTTP hook receives route-template/method metadata after request completion. The provider hook measures completion calls or stream preparation through acquisition of the first chunk; it does not claim to measure the whole streamed response. Hook failure is logged and does not replace the customer response. Context attributes must remain bounded and low-cardinality and must not include content, secrets, authorization headers, or customer-controlled raw strings.

An operator/customer build must test audit and observability behavior under outage and backpressure. The base contract does not authorize silent loss of required compliance audit.

## Minimal validation example

```python
# SPDX-License-Identifier: AGPL-3.0-only
from portable_ai_gateway.extensions import (
    AUDIT_SPI_VERSION,
    AuditSink,
    validate_extension,
)


class CustomerAuditSink:
    spi_version = AUDIT_SPI_VERSION

    def emit(self, event):
        write_metadata_only_event(event)


sink = CustomerAuditSink()
validate_extension(sink, AuditSink, AUDIT_SPI_VERSION)
```

`write_metadata_only_event` is deliberately customer-supplied pseudocode, not a bundled dependency.

## Customer build checklist

1. Pin extension source and dependencies; establish their license/provenance and include them in SBOM/notices.
2. Keep secrets and policy payload content out of constructors, retained state, reprs, logs, errors, metrics labels, traces, and build arguments.
3. Override the relevant low-level `GatewayComposition` factory. Keep the inherited higher-level wiring so `validate_extension` and the runtime adapters execute before serving traffic.
4. Run the core conformance suite plus extension-specific negative, outage, concurrency, redaction, and tenant-isolation tests.
5. Rebuild/sign a new image, publish corresponding source as required, and update `PAG_SOURCE_URL`.
6. Deploy by immutable digest and retain rollback artifacts.

The executable PoC `test_customer_composition_replaces_all_six_extension_families` in `tests/test_runtime_extensions.py` supplies customer implementations for all six families through a `GatewayComposition` subclass. It proves authentication, content-aware request policy with defensive-copy isolation, constrained routing, provider execution, audit emission, and both observability callbacks on a real data-plane request without editing the gateway domain. `test_customer_request_guardrail_denies_by_content_before_provider` proves a customer policy can deny based on request content while the audit trail remains content-free.

Whether a separately developed extension and its distribution/network use create AGPL obligations is a fact-specific legal question. The commercial license cannot grant rights over third-party extension code; obtain qualified advice.

Breaking argument, return, semantics, or safety changes require a new version identifier. Existing versioned contracts must remain testable for their documented support window; an alpha release may deprecate a contract only through release notes and a migration path.
