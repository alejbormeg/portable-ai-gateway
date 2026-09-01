<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Architecture

## Design goals

Portable AI Gateway separates product policy from provider-specific behavior. Customers can replace adapters and deployment integrations while retaining a stable public contract. The same first-party source and image serve every license and deployment profile.

The alpha prioritizes customer ownership, open interfaces, tenant isolation, metadata-minimized operations, replaceable providers, reproducible artifacts, and explicit failure over silent compatibility loss. It does not claim multi-region availability, production readiness, or a validated 99.9% SLO.

## Runtime topology

```text
                         administrative identity (OIDC)
                                      |
                              +-------v--------+
 Git desired state ---------> | control plane | <----> PostgreSQL
             CLI apply/export +-------+--------+          ^
                                      | versioned config   |
                                      v                    |
 client --> TLS/auth --> +------------+----+               |
                         |   data plane    | ---------------+
                         +---+----------+--+
                             |          |
                    Valkey/cache       ordered route
                                        |
                           +------------+-------------+
                           |            |             |
                          mock   OpenAI-compatible   Vertex AI
```

One OCI image exposes three entry modes:

- `data-plane`: serves inference and discovery endpoints. It does not provide administrative mutation APIs.
- `control-plane`: validates and manages configuration, credentials metadata, and administrative resources.
- `cli`: bootstraps, validates, applies, exports, diagnoses, and checks provider connectivity using the same domain contracts.

PostgreSQL is the durable source of truth. Valkey or an explicitly selected Redis-compatible service is disposable infrastructure for cache, ephemeral coordination, and rate limiting; losing it must not redefine durable configuration. Provider secrets are referenced by environment variable name and are never included in the portable configuration document.

The alpha PostgreSQL adapter serializes its synchronous DB-API operations. Its readiness probe makes one safe reconnect attempt only when no administrative transaction is open, then verifies the replacement connection; it never retries an in-flight mutation because that could duplicate a side effect. A measured production pool/reconnection policy remains a release-engineering gate.

## Request flow

1. The edge terminates TLS and forwards a bounded request to the data plane.
2. Authentication resolves an actor, tenant, and scopes. Anonymous access is allowed only when explicitly enabled outside production.
3. The gateway derives an immutable alpha capability requirement set from the request, then checks tenant status, the public model declaration, per-target declarations, policy, quota, and the matching provider/target residency attestation. Model admission and target fallback consume the same detected requirements.
4. Routing selects eligible targets in ascending priority order.
5. A provider adapter translates only provider-specific transport concerns. It must not silently remove requested semantics.
6. Before the first response byte, a retryable target failure may select the next eligible target. Once streaming output begins, the gateway never replays the request to another provider.
7. The gateway returns the provider-compatible response and emits a content-free audit event.

`/v1/responses` is routed only to a target that declares native Responses support. The alpha does not reconstruct Responses semantics from Chat Completions.

Capability detection is an explicit admission boundary, not provider negotiation. In the alpha it recognizes only the tool, structured-output, multimodal, and streaming indicators documented in the [HTTP API contract](api.md#capability-admission-in-the-alpha). Function parameter schemas remain inside the tools boundary and are not reclassified as structured output. Unknown future provider dialects require an intentional contract update and conformance evidence.

## Control and GitOps flow

Configuration uses a versioned `GatewayConfiguration` document. In imperative mode, authorized administrators may use `/admin/v1` or the CLI; a mounted file seeds only an empty database and PostgreSQL then remains authoritative. In GitOps mode, the checked-in document is desired state: mutating administrative calls that would drift from it are rejected, and reconciliation transactionally upserts declared configuration resources and prunes omitted ones. Tenant prune/delete revokes its keys and removes its owned configuration; audit history remains. Export is intended for inspection and backup, not for copying secret values.

See [GitOps](gitops.md) for conflict and rollback behavior.

## Extension boundaries

Provider, authentication, policy, routing, audit, and observability extensions depend on versioned contracts, not FastAPI request objects or storage implementation details. Both application factories accept the versioned `GatewayComposition` build-time injection seam, so a customer image can replace those adapters without changing domain schemas/services. Extensions run in-process in the alpha and therefore share the gateway's trust level. A third-party extension must be reviewed, pinned, included in the SBOM, and rebuilt into a new image; runtime downloading is deliberately excluded.

For a non-streaming request, and for the preparation of a streaming request before its first byte, `PAG_UPSTREAM_TIMEOUT_SECONDS` is one monotonic deadline across every ordered provider attempt. A retryable first provider therefore consumes the same budget as its fallbacks; the gateway returns `gateway_deadline_exceeded` rather than multiplying the timeout. Once the first streaming byte is sent, fallback is forbidden and the stream follows its own upstream/client lifecycle.

Concurrency slots use a 60-second cache lease renewed while an admitted request remains active. The renewal is ownership-bound: an expired or replaced token cannot refresh another request's slot. If renewal is rejected or the cache becomes unavailable, a live stream detects the lost lease before its next emitted chunk, records a controlled interruption, and releases its local state. This is intentionally fail-closed; availability of the cache remains an operational dependency for tenant concurrency quotas.

See [Extensions](extensions.md) for compatibility and failure rules.

## Deployment invariants

- License choice never changes code paths or artifact content.
- The image runs as a non-root user, does not require Linux capabilities, and writes only to declared writable storage.
- Configuration and secrets are injected; they are not baked into the image.
- Production requires a source URL, API-key pepper, and OIDC verification material.
- Health shows process liveness; readiness is the traffic admission signal.
- Logs and audits omit prompts, responses, tool payloads, authorization headers, and credentials.
- All external dependencies must have bounded timeouts and must fail closed for authorization and tenant isolation.

These are design contracts. Their implemented and tested status is recorded in the [PoC results](../.ai-ra/pocs/portable-ai-gateway-productization/results.md).

## Explicit non-goals for `v0.1.0-alpha`

- Bundling or operating a local model server.
- Emulating unsupported provider capabilities.
- A hosted vendor control plane, billing service, or telemetry collector.
- Dynamic download of plugins or policy code.
- Prompt/response retention, semantic caching, or model-output analytics.
- A claim of workload portability merely because manifests exist.
- Production SLA, LTS, or backward-compatibility guarantees.
