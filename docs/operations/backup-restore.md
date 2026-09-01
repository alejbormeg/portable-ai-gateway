<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Backup and restore

PostgreSQL is the durable source of truth. Back up its schema and data using the platform's consistent snapshot or PostgreSQL-native tooling. Valkey/Redis-compatible state is not authoritative and should normally be rebuilt rather than treated as the only recovery source.

## Backup contract

Define RPO/RTO, encryption key ownership, retention, region, immutable/offline copy, restore identity, and deletion policy. Backups can contain tenant, credential metadata, and audit data even though they must not contain prompts/responses; classify and protect them accordingly.

A backup is not accepted until a restore into an isolated environment has succeeded and verified schema version, resource counts, tenant boundaries, key revocation/expiry metadata, configuration version, and a synthetic canary. Never run restore drills against the only production database.

## Restore sequence

1. Freeze administrative mutation and capture the approved image/config versions.
2. Provision an isolated compatible PostgreSQL target with least-privilege identity.
3. Restore without connecting data-plane traffic.
4. Validate database integrity and migration compatibility using metadata only.
5. Start control/data processes against the isolated target and run negative auth/isolation plus synthetic inference checks.
6. Rotate credentials if the incident may have exposed backup or connection material.
7. Cut over through a reversible change and monitor error, latency, quota, and audit signals.
8. Retain or destroy the old target according to incident/legal requirements and record verification.

Exact commands vary by managed or self-hosted PostgreSQL and belong in the operator's protected environment documentation.
