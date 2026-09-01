<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Portable AI Gateway

> Status: **pre-release `v0.1.0-alpha`**. This project is not production-ready, has no SLA, and has not yet completed the published portability, security, or performance validation plan.

Portable AI Gateway is a customer-owned, self-hosted gateway for routing OpenAI-compatible AI traffic across GCP Vertex AI, customer-provided OpenAI-compatible endpoints, and a deterministic mock provider. The gateway is designed so that customers can inspect, modify, build, and operate every first-party layer without a vendor-hosted control plane, license server, phone-home requirement, or commercial feature gate.

The same source and OCI image are offered under `AGPL-3.0-only` and, by separate agreement, a commercial license. Commercial terms grant different usage rights; they do not unlock different features. See [LICENSING.md](LICENSING.md).

## What this alpha is intended to provide

- Separate stateless data-plane and administrative control-plane modes from one OCI image.
- OpenAI-compatible `Chat Completions` as the portable inference contract.
- Native-only `Responses` forwarding when the selected provider declares that capability; no lossy emulation.
- Vertex AI, generic OpenAI-compatible, and deterministic mock provider adapters.
- PostgreSQL as the source of truth and Valkey/Redis-compatible storage for ephemeral cache and rate-limit state.
- Tenant-scoped API keys, OIDC/OAuth administration, ordered routing and pre-first-byte fallback.
- Metadata-only audit events without prompts, responses, tool arguments, credentials, or bearer tokens.
- Docker Compose, Kubernetes/OpenShift Helm, and GCP Cloud Run deployment assets.
- Versioned extension contracts for providers, authentication, policy, routing, audit, and observability.

This is a target contract for the alpha. A capability is not considered validated merely because it appears in this list. Follow [the evidence record](.ai-ra/pocs/portable-ai-gateway-productization/results.md) for executed results.

## Start locally

The development profile uses synthetic configuration and the mock provider. It requires no cloud credentials and performs no external model call.

```sh
# SPDX-License-Identifier: AGPL-3.0-only
git clone <YOUR_REPOSITORY_URL>
cd portable-ai-gateway
docker compose up --build
```

At startup, both gateway modes validate the mounted synthetic configuration; the first process to find the empty PostgreSQL store seeds it, and later imperative state is preserved because this profile is not GitOps-authoritative. After the containers report ready, follow [the development quickstart](docs/deployment/development.md) to send a deterministic request. Do not publish a repository while `<YOUR_REPOSITORY_URL>` or any release-gate placeholder remains unresolved.

## Deployment paths

| Profile | Intended use | Entry point | External change |
| --- | --- | --- | --- |
| Development | Laptop evaluation with mock provider | [Development quickstart](docs/deployment/development.md) | None after local images exist |
| On-prem | Docker Compose or Kubernetes/OpenShift with a customer-provided model endpoint | [On-prem quickstart](docs/deployment/on-prem.md) | Cluster changes require operator approval |
| GCP | Cloud Run, Vertex AI, Cloud SQL and a Redis-compatible managed service | [GCP quickstart](docs/deployment/gcp.md) | Creates billable resources only after explicit `terraform apply` |
| Air-gapped | Transfer and verify a pre-built offline bundle | [Air-gap guide](docs/air-gap.md) | No network access during installation |

The same image digest is intended for every deployment profile. Deployment-specific behavior comes from configuration, identity, and adapters—not separate editions.

## Public interfaces

- Data plane: `GET /`, `GET /health`, `GET /ready`, `GET /v1/models`, `POST /v1/chat/completions`, and `POST /v1/responses`.
- Control plane: `/admin/v1/...` resources for tenants, providers, models, routes, policies, quotas, and API keys.
- Process modes: `data-plane`, `control-plane`, and `cli`.
- Source disclosure: official production deployments set `PAG_SOURCE_URL`; `GET /`, `GET /health`, and the HTTP `Link: <...>; rel="source"` header identify the corresponding source. Network-deployed forks must update this value.

The compatibility boundary, errors, streaming guarantees, and alpha limitations are described in [API contract](docs/api.md). Configuration names and precedence are in [Configuration](docs/configuration.md).

## Documentation

- [Architecture and data flow](docs/architecture.md)
- [Product positioning and comparison boundaries](docs/positioning.md)
- [API contract](docs/api.md)
- [Authentication and authorization](docs/authentication-authorization.md)
- [Extension contracts](docs/extensions.md)
- [GitOps operation](docs/gitops.md)
- [Audit and privacy](docs/audit-privacy.md)
- [Threat model](docs/threat-model.md)
- [Operator runbook](docs/operations/runbook.md)
- [Testing and benchmarks](docs/testing-and-benchmarks.md)
- [Release process and legal gates](docs/release.md)

## Security, support, and governance

Security reports must use the private process in [SECURITY.md](SECURITY.md), not a public issue. Community support is best effort and carries no response-time or availability commitment; see [SUPPORT.md](SUPPORT.md). External pull requests are not accepted during the initial dual-license phase; issues and responsible disclosures are welcome under [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Unless a file says otherwise, this repository is licensed under the GNU Affero General Public License v3.0 only (`AGPL-3.0-only`). A commercial alternative for the same code may be available from the copyright holder. The copyright-holder identity is an unresolved publication gate and is deliberately not invented in this repository.

Nothing in this repository is legal advice. Complete the gates in [the release guide](docs/release.md) with qualified counsel before public distribution or commercial contracting.
