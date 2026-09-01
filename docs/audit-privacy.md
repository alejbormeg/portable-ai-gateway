<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Audit and privacy

The audit contract is metadata-only by design. It supports accountability and incident reconstruction without turning the gateway into a prompt/response datastore.

## Allowed audit fields

An event may contain event and request IDs, timestamp, actor, tenant, action, resource, model alias, route, routing/policy decision, selected provider, HTTP/result status, error code, and configuration version. Operational metrics may aggregate counts, latency, token usage reported by a provider, and status dimensions only when they cannot reconstruct content.

## Prohibited content

Audit events, logs, traces, metrics labels, exception messages, and support bundles must not contain:

- prompts, messages, responses, embeddings, images, files, or tool arguments/results;
- request/response bodies or content-derived snippets/hashes;
- API keys, bearer tokens, cookies, database URLs, OIDC secrets, cloud credentials, or authorization headers;
- provider opaque metadata unless explicitly classified and approved;
- personal or customer data not essential to the allowed identity fields.

Request IDs, model aliases, tenant IDs, resources, actions, routes, providers, error codes, configuration versions, and actor values are length/character constrained before persistence to prevent content smuggling and log injection. Actor and tenant identifiers should be stable pseudonymous IDs rather than email addresses where practical.

## Retention and access

The product does not impose a universal retention period. Each operator must define purpose, lawful basis, retention, deletion, access roles, residency, export, and incident procedures before processing non-synthetic traffic. Grant audit read access separately from gateway administration and inference use. The base control plane accepts the OIDC-only `audit:read` scope for `/admin/v1/audit`; that scope does not authorize other administration. Encrypt storage and transport and monitor bulk export.

A support request should include only sanitized metadata. Operators must not send raw production logs until they have reviewed them against this contract.

## Validation

Tests must inject canary secrets and synthetic content, exercise success/error/stream/tool paths, then assert zero prohibited values across logs and audit sinks. Passing code review alone is insufficient. The planned evidence and result status are in the [PoC record](../.ai-ra/pocs/portable-ai-gateway-productization/results.md).

The alpha records authenticated and unauthenticated denials, successful administrative reads/mutations, authenticated administrative validation/conflict/GitOps/not-found failures, CLI configuration mutations, routing/provider outcomes, and the terminal result of a stream. An insufficient-scope JWT retains its verified actor rather than being relabeled as unauthenticated. With the first-party database audit implementation, an administrative or CLI mutation and its audit event share one SQLite/PostgreSQL transaction: either both commit or both roll back. A customer-supplied `AuditSink` executes inside that boundary, so a raised sink error rolls back the first-party database changes, but external side effects cannot participate in or be retracted by the database transaction. In particular, a sink emission can succeed before a later database commit failure. External sinks therefore require idempotency and reconciliation appropriate to their delivery system; this alpha does not provide a transactional outbox.
