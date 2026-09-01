<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Third-party notices

Portable AI Gateway includes or interoperates with third-party software. Each third-party work remains subject to its own license; the project's AGPL/commercial dual-license offer does not alter those terms.

## Release rule

This hand-maintained file is not, by itself, a complete dependency inventory. Every release must include:

- an SPDX or CycloneDX SBOM generated from the exact immutable image digest;
- a dependency-license report covering Python packages, base images, Helm artifacts, and bundled binaries;
- all attribution, notice, and source-offer material required by those dependencies;
- a review showing that runtime licenses are compatible with both AGPL distribution and the proposed commercial license.

Unknown, ambiguous, non-commercial, source-available, or strong-copyleft runtime dependencies require legal review and block publication. Build-only dependencies must still be recorded when their terms require it.

## Separately distributed infrastructure

Development and deployment examples may orchestrate, but do not relicense:

| Component | Intended role | Upstream license to verify for each pinned version |
| --- | --- | --- |
| PostgreSQL | Durable source of truth | PostgreSQL License |
| Valkey | Packaged Redis-compatible cache and rate-limit state | BSD-3-Clause |
| Redis-compatible service | Optional externally operated integration | Operator must verify the selected product/version terms |
| Kubernetes / OpenShift | Optional container orchestration | Component-specific upstream terms |
| GCP managed services | Optional deployment target | Provider service terms, not an open-source software license |

The presence of a name here does not state that a component is embedded in every artifact. The release SBOM is authoritative for artifact contents.

## Declared direct Python dependencies

The source manifest currently declares the following dependency families. Licenses below are expected upstream identifiers for review, not a substitute for the metadata and license texts of the exact resolved artifacts:

| Package family | Role | Expected upstream license; verify exact release |
| --- | --- | --- |
| FastAPI | HTTP API framework | MIT |
| HTTPX | Provider HTTP client | BSD-3-Clause |
| Pydantic | Schemas and validation | MIT |
| PyYAML | Configuration parsing | MIT |
| Uvicorn | ASGI server | BSD-3-Clause |
| pg8000 | Optional pure-Python PostgreSQL adapter | BSD-3-Clause |
| redis-py | Optional Valkey/Redis-compatible client | MIT |
| google-auth | Optional Vertex workload identity | Apache-2.0 |
| cryptography | Optional RS256 verification | Apache-2.0 OR BSD-3-Clause, plus applicable bundled native-component terms |
| Hatchling | Build backend | MIT |
| pytest / pytest-cov | Test tooling | MIT |

Transitive packages—including the exact dependency closure selected by pg8000—Python itself, base-image operating-system packages, native libraries, GitHub Actions, Terraform providers, Helm, and release tools are not enumerated by this table. Their versions, licenses, notices, and source locations remain pending until a locked resolution and exact-artifact SBOM are produced; they must appear in the applicable source/image/build inventory.

## Pending release inventory

`v0.1.0-alpha` must not be published until automated inventory supplements this file with exact package names, versions, source locations, licenses, and required notices for the resolved artifact. No third-party copyright owner endorses Portable AI Gateway.
