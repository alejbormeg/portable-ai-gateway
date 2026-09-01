<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Cleanup plan — Portable AI Gateway productization

Status: `verified-no-external-resources`; local source validation created only ignored interpreter/test caches.

## Ownership and safety

The PoC operator owns only resources created specifically for an approved execution and listed in its evidence. Existing containers, volumes, clusters, cloud projects, services, registries, databases, identities, secrets, or prior PoC resources are out of scope and must not be modified or deleted.

Resolve exact names, labels, project/namespace, and creation evidence before any destructive command. Cleanup is not complete until absence is verified through a read-only inventory and retained data has followed the approved retention policy.

## Planned local resources

| Resource | Expected owner | Cleanup order | Verification | Current state |
| --- | --- | ---: | --- | --- |
| Gateway data/control containers | PoC Compose project | 1 | Project-scoped container list is empty | Not created |
| Synthetic provider/test containers | PoC Compose project | 2 | Project-scoped container list is empty | Not created |
| PostgreSQL/Valkey containers | PoC Compose project | 3 | Project-scoped container list is empty | Not created |
| PoC network | PoC Compose project | 4 | Named network absent | Not created |
| PoC PostgreSQL/Valkey volumes | PoC Compose project | 5, only after evidence retention approval | Exact named volumes absent | Not created |
| Locally built gateway image/bundle | PoC execution | 6, optional and explicitly targeted | Exact digest/path absent or retained by decision | Format-only temporary bundle created under `mktemp` and removed; no image/release bundle created |
| Raw synthetic result files | PoC execution | After review/retention decision | Manifest records retained or deleted state | Markdown evidence retained in repository |
| Python `__pycache__` / pytest cache | Local source gate | Optional after evidence review | Ignored project-local cache paths | Created and intentionally retained; reproducible and non-sensitive |

Use the repository's final project-scoped Compose cleanup command after inspecting resolved resources. Do not use broad prune commands. Local image/dependency caches are not automatically deleted because they may be shared.

## Future external resources

Each approved GCP or OpenShift execution must append an exact inventory here before creation: provider/project/region or cluster/namespace, resource ID, labels, owner, cost center, dependencies, retention, destroy command, verification command, and final state. Terraform destroy or namespace deletion requires a fresh explicit approval and plan review.

No external resource exists merely because Terraform or Helm files are present. The executed commands did not start a container, contact a cloud/cluster, publish an artifact or create a billable resource, so no external cleanup action exists for this run. Do not claim future cleanup until read-only verification succeeds; record partial failures as open cleanup actions with owner and cost exposure.
