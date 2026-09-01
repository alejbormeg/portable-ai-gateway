<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Roadmap

This roadmap communicates intent, not a delivery promise.

## `v0.1.0-alpha` — portable product baseline

- One image with data-plane, control-plane, and CLI modes.
- Mock, generic OpenAI-compatible, and Vertex AI provider adapters.
- Chat Completions plus capability-gated native Responses forwarding.
- PostgreSQL source of truth, Valkey/Redis-compatible ephemeral state, OIDC, scoped API keys, quotas, metadata-only audit, ordered fallback, and GitOps mode.
- Compose, Helm/OpenShift, GCP Terraform, air-gap packaging, SBOM, signature, and provenance workflows.
- Execute and publish the functional, isolation, failure, portability, offline, reproducibility, and performance evidence plan.

## Later alpha/beta candidates

- Measure the implemented bounded pg8000 pool under real PostgreSQL failure/load conditions; it uses 1–5 connections per process, health-checks idle connections, and never retries an in-flight mutation.
- Measure lease-renewal behavior against real Valkey failures and long-lived streams; the local runtime now uses a short ownership-bound lease with renewal and fails closed when it is lost.
- Measure the configured end-to-end deadline under real provider and streaming conditions; the local code now enforces one monotonic budget across ordered pre-response provider attempts.
- Stabilize extension APIs and publish compatibility fixtures.
- Add upgrade, rollback, backup/restore, and multi-region operational evidence.
- Establish an LTS and deprecation policy only after real adopters validate upgrade needs.
- Consider additional providers and policy integrations through public extension contracts.
- Open external contributions only after the CLA and maintainer governance are legally approved.

A production-ready or 1.0 milestone will require independently reviewed security, operability, performance, recovery, upgrade, and license-compliance evidence. The 99.9% availability target remains a design objective until a measurement window and enforceable SLO are approved.
