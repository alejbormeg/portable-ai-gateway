<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Requirement-to-evidence traceability

This matrix connects the approved product requirements to implemented mechanisms, executable checks, and the current evidence boundary. `Implemented` is not synonymous with cross-platform validation.

| Requirement | Capability / pattern | Implementation | Executed local evidence | Remaining acceptance evidence |
| --- | --- | --- | --- | --- |
| `PAG-REQ-001` — customer-owned/self-hosted | One source tree; no licensor dependency; offline identity material; corresponding-source link | `LICENSE`, `LICENSING.md`, `Dockerfile`, `Settings`, `JWTVerifier`, air-gap scripts | No phone-home/license path found; relocatable image-only three-record bundle passes format and extracted Compose resolution | Real signed disconnected install/run, zero-egress capture and source-to-artifact verification |
| `PAG-REQ-002` — identical AGPL/commercial features | One build/release path with no license-conditioned runtime | `Dockerfile`, candidate/validation/release workflows, release gates, licensing docs | Static scan and workflow contracts prove one path; reproducibility/parity evidence is required and hash-bound | Two clean builds, exact OCI digest/scans, approved commercial agreement and legal inventory |
| `PAG-REQ-003` — portable deployment | Configuration-driven single image; Compose, Cloud Run, Helm/OpenShift and air-gap adapters | `compose.yaml`, `deploy/`, `infra/terraform/gcp/`, bundle scripts | Base/BYO/air-gap Compose resolution and three Helm lint profiles pass; no container/deployment ran | Same digest on Compose, Cloud Run, Kubernetes/kind, OpenShift restricted and disconnected target |
| `PAG-REQ-004` — replaceable adapters | `pag.composition/v1`, six lower-level SPIs, provider factory and three built-ins | `composition.py`, `extensions.py`, `providers/`, routing/service composition | Composition injection plus 27 conservative tools/structured/multimodal capability tests pass | Clean customer adapter build, real outage/security tests and rebuilt-image evidence |
| `PAG-REQ-005` — identity/isolation/audit | OIDC, scoped expiring keys, target capability/residency filtering, quotas and typed metadata audit | `security.py`, `routing.py`, `cache.py`, `schemas.py`, atomic store/admin/CLI paths | Negatives cover tenant/key/audit/fallback/cache/JWKS; production stateful CLI requires OIDC and first-party mutation/audit rollback is tested | Real RS256 IdP, PostgreSQL, Valkey, provider, logs/traces/support-bundle scan and adversarial platform tests |

The executable mapping additionally includes `test_capability_detection.py`, `test_entrypoint.py`, `test_airgap_scripts.py`, `test_release_evidence.py` and `test_release_workflow_contract.py`. Exact commands and checksums are in `evidence/local-validation.md`; conclusions and unsupported claims are in `results.md`.

## Governance boundary

This is a clean public-product repository, so identifiers and history from the private corporate proof of concept are deliberately not imported. The public chain above starts at stable `PAG-REQ-*` product requirements. A separately controlled objective-to-requirement crosswalk has been drafted outside this repository, but it is not release evidence until reviewed and bound to the final commit/digest. Private objective identifiers remain intentionally absent from the distributable source tree.
