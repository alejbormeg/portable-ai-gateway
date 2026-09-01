<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# GitOps mode

GitOps mode makes a versioned `GatewayConfiguration` document the desired-state authority. PostgreSQL remains the runtime source of truth after an apply; Git is authoritative for what should be present.

## Workflow

1. Change a non-secret YAML document under your deployment repository.
2. Run the CLI validation command in CI without provider credentials where possible.
3. Review schema, tenant scope, capability, residency, license, and security implications.
4. Record the immutable commit in `PAG_CONFIG_VERSION`.
5. Apply through an OIDC-authenticated automation identity with the minimum administrative scope.
6. Verify readiness, exported metadata, audit events, and a synthetic canary.
7. Roll back by applying a reviewed earlier Git revision—not by editing database rows.

## Authority and conflicts

When `PAG_GITOPS_MODE=true`, administrative mutations that would create drift are rejected. The CLI `bootstrap` command is also rejected; `config apply` is the sole configuration mutation path. Read, validate, export, health, and diagnostic operations remain available according to scope. Emergency changes still require a Git commit; if an operator must deviate, first disable GitOps through controlled deployment configuration, record the incident, then reconcile before re-enabling it.

Apply validates the complete document, then transactionally reconciles all configured tenants, providers, models, routes, policies, and quotas: declared IDs are upserted and omitted IDs are pruned. Audit history is preserved. Keys for retained tenants are preserved; pruning a tenant atomically revokes its keys and removes its tenant-owned configuration so reusing the ID cannot revive old credentials. PostgreSQL uses an advisory transaction lock so concurrent data/control startup reconcilers serialize rather than interleave. Resource IDs are stable identities; renaming an ID is a delete/create operation and should be reviewed for API-key, audit, and route impact. Secret values are never exported or committed.

Outside GitOps, a mounted file is an empty-database seed rather than a restart-time authority. Subsequent control-plane changes remain in PostgreSQL and receive an `imperative@...` configuration revision. Switching authority modes is an operational migration and requires an exported-state review before enabling reconciliation.

## Repository hygiene

Protect the desired-state branch, require review, sign commits/tags where supported, scan for secrets, and bind deployment identity to the repository/environment. Keep environment overlays explicit and avoid templating that makes the rendered document impossible to review. Store the rendered, validated document as release evidence.

Git availability must not sit on the data-plane request path. A Git or CI outage prevents reconciliation but should not, by itself, authorize or alter inference traffic.
