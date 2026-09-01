<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Operator runbook

This runbook is an alpha baseline, not an SLA. Platform owners must adapt commands, escalation, recovery objectives, and evidence retention to their environment.

## Pre-deployment gate

Record the immutable image and chart digests, version, SBOM/signature verification, approved configuration revision, database backup/restore result, source URL, change owner, rollback artifact, maintenance window, and synthetic canary. Confirm no unresolved legal placeholder appears in a public release.

Never deploy a mutable image tag alone. Never test a production incident with real prompts or by printing secrets.

## Health model

- `GET /health` answers whether the process is alive and exposes the configured corresponding-source link. Do not send traffic solely because liveness succeeds.
- `GET /ready` is the traffic-admission signal. Both modes require store/cache connectivity; the data plane also requires at least one active provider and route. A non-ready instance should be removed from service while preserving enough metadata-only diagnostics to identify the dependency.
- A synthetic inference through the mock or an approved canary model validates more than health, but must use a dedicated tenant and non-sensitive payload.

Capture request ID, timestamp, component/mode, image digest, config version, tenant ID, model alias, route/provider ID, status/error code, and latency. Do not capture bodies or credentials.

## Readiness failure

1. Stop rollout or remove the new revision from traffic; do not restart all healthy replicas simultaneously.
2. Check startup validation for missing/invalid production settings.
3. Verify PostgreSQL reachability, credentials, migration compatibility, and capacity. TCP production URLs require an accepted `sslmode`; Cloud SQL uses the local `/cloudsql/` Unix socket.
4. Verify desired-state file readability and schema/version validity.
5. Treat Valkey/cache failure according to observed implementation behavior; never reconstruct durable state from cache.
6. Compare image digest and `PAG_CONFIG_VERSION` with the approved release.
7. Roll back image and configuration together if compatibility is uncertain.

## Provider degradation and fallback

1. Identify affected provider, model alias, residency, status class, and whether a stream emitted its first byte.
2. Verify provider identity/quota and outbound network/DNS without logging credentials.
3. Confirm the next target satisfies tenant capability and residency policy.
4. Fallback may occur only for classified retryable failures before the first byte. Never replay a partially emitted stream.
5. Disable a failing target through reviewed desired state if repeated failures cause latency or quota amplification.
6. Restore using a synthetic canary and audit the configuration change.

## Authentication failures

For widespread OIDC failures, compare issuer, audience, key ID, signature material, clock skew, expiry, and edge forwarding. Fail closed; do not enable anonymous development mode or API keys for a sensitive tenant as an emergency bypass.

For an API key incident, revoke the exact key, preserve metadata audit evidence, rotate the client credential, and investigate scope/tenant use. Rotating `PAG_API_KEY_PEPPER` can invalidate every key and requires an approved fleet-wide plan.

## Quota/cache failures

When cache state is unavailable, verify whether requests fail closed or use a documented degraded policy. Do not assume rate or cross-replica concurrency limits remain enforced. Protect providers with edge/platform limits while recovering the service. A crashed request can retain a concurrent lease until its defensive one-hour TTL, temporarily reducing available tenant capacity; investigate before deleting keys manually. Replacing cache must not alter tenants, routes, policies, or durable key metadata.

## Database incident

Freeze administrative writes and rollout. Determine whether the data plane can safely continue with known configuration; if consistency is uncertain, remove it from traffic. Restore into a separate instance, run integrity and version checks, then cut over through an approved change. Follow [backup and restore](backup-restore.md).

## Rollback

Rollback is a paired operation over image digest, schema compatibility, and configuration revision. Verify the older binary can read the current database schema before routing traffic. Apply an earlier Git revision instead of manually editing rows. Run health, readiness, auth-negative, tenant-isolation, and synthetic inference canaries after rollback.

## Evidence and closure

Record a metadata-only timeline, affected versions/tenants, control decisions, rotations, residual risks, and follow-up tests. If confidentiality or integrity may be affected, use [incident response](incident-response.md) and the organization's notification process.
