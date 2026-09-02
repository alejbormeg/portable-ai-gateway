<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Threat model

Status: initial alpha model; local source-level validation is recorded, while real-service, adversarial and platform validation remains incomplete.

## Assets and trust boundaries

Protected assets include provider credentials, OIDC verification material, API-key hashes and pepper, tenant/configuration state, routing/residency policy, audit integrity, source/build provenance, and service availability. Prompts and responses are transient high-sensitivity data even though the gateway must not persist them.

Trust boundaries exist at the public edge, data plane, control plane, CLI/CI identity, PostgreSQL, Valkey/Redis-compatible cache, provider egress, extension code, observability pipeline, image registry, and operator workstation. Customer-provided model endpoints and extensions are not inherently trusted.

## Primary threats and required controls

| Threat | Example | Required controls | Failure posture |
| --- | --- | --- | --- |
| Tenant confusion | Key for tenant A reads/routes as tenant B | Server-derived tenant context, scoped keys/tokens, negative isolation tests | Deny |
| Administrative takeover | Forged/replayed OIDC token changes routes | Exact issuer/audience/signature validation, expiry checks, TLS, least privilege, audit | Deny |
| Credential theft | Secret appears in config/log/error | Secret references, platform secret store, redaction, canary tests, rotation | Stop/rotate |
| SSRF / malicious upstream | Arbitrary provider URL reaches metadata/internal service or receives an ADC token | Parsed HTTPS endpoints, exact loopback-only HTTP, syntax-checked Vertex project/location, validation of the resolved Vertex host, exact egress allow-list, DNS/network policy | Deny |
| Residency bypass | Fallback leaves approved location | Capability and residency filtering before routing; no unrestricted emergency fallback | Fail request |
| Cross-provider stream replay | Failure after partial output triggers fallback | Fallback only before first byte | Terminate stream |
| Resource exhaustion | Oversized request/response, slow stream, quota bypass | Request body/time/concurrency/rate limits, incremental provider reads, accumulated per-response/per-stream limit, edge limits | 4xx/429/502/timeout |
| Audit leakage/injection | Prompt or control characters enter logs | Typed allow-list event, sanitized IDs, no body logging, content canaries | Drop unsafe field/event |
| Supply-chain compromise | Replaced dependency/image/chart | Pinning, review, SBOM, signatures, provenance, digest deployment, offline verification | Block release/deploy |
| Extension compromise | In-process plugin exfiltrates data | No runtime downloads, review/pin/rebuild, SBOM, narrow contract, egress controls | Disable/rebuild |
| GitOps drift | Direct admin mutation defeats reviewed state | GitOps mutation lock, immutable config version, audit, reconcile/rollback | Reject mutation |
| Source-offer mismatch | Modified service points to upstream source | Production `PAG_SOURCE_URL`, release check, operator update obligation | Block production startup/release |

## Abuse and model risks

The gateway enforces transport, identity, routing, and configured policy. It does not make an upstream model safe, correct, unbiased, or suitable for a regulated use case. Prompt injection, tool abuse, data exfiltration through model output, harmful content, and model-specific jailbreaks require workload-specific controls outside and, where appropriate, through the policy extension contract.

## Known alpha gaps

The alpha has not yet demonstrated penetration resistance, isolation under adversarial concurrency, multi-region recovery, production OIDC interoperability, safe third-party extensions, platform-specific network isolation, dependency provenance, or the stated performance targets. The PostgreSQL adapter is synchronous and uses a bounded per-process pool; safe idle replacement and synthetic pool tests exist, but real load, failure/recovery and multi-replica validation against PostgreSQL remain release engineering work. The Compose gateways require a non-internal bridge for loopback port publication on current Docker Engine, so application allow-lists do not replace host/cluster egress policy. First-party administrative and CLI mutations share a database transaction with their first-party audit event, and configuration reads use revision snapshots in the implemented adapters. A customer-supplied external `AuditSink` is not transactionally coupled: an emitted external event cannot be retracted if the later database commit fails, and no outbox is provided. Upstream response limits have source-level negative tests but not adversarial real-service/load validation. Static manifests and SQLite/mock tests do not close these gaps.

## Review triggers

Update this model for every new provider, authentication method, extension execution mode, stored data class, externally reachable endpoint, deployment platform, control-plane mutation, or supply-chain tool. Security review and executed negative tests are release gates.
