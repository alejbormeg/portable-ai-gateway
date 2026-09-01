<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Changelog

All notable changes will be recorded here. The project intends to use Semantic Versioning after the public contract is stabilized.

## [Unreleased]

### Added

- Initial product, deployment, governance, licensing, security, and PoC evidence structure for the `v0.1.0-alpha` target.
- Exact multi-platform candidate/SBOM evidence, reproducible-build and external-harness contracts, standalone three-image air-gap packaging, and resumable byte-verified release publication.
- Conservative request-capability detection, atomic first-party state/audit mutations, production CLI OIDC identity, and fail-closed malformed-JWKS handling.

### Validation status

- The local source gate records 120 passing tests plus static Compose/Helm/configuration, workflow/shell contracts and a relocatable format-only air-gap check. Real-service, deployment, offline-runtime, exact-artifact, reproducibility and performance evidence remains inconclusive; see `.ai-ra/pocs/portable-ai-gateway-productization/results.md`.
