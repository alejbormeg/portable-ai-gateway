<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Security policy

## Supported versions

No version is currently declared production-ready. During the `v0.1.0-alpha` cycle, only the latest commit on the default branch and the latest alpha artifact are candidates for best-effort security fixes. There is no security SLA, paid bounty, or guaranteed embargo period.

## Report a vulnerability privately

Use GitHub's **Report a vulnerability** / private security advisory flow for this repository. Do not open a public issue for a suspected vulnerability. If that flow is unavailable, email <alejbormeg@gmail.com> with a subject starting with `[security]`.

Include the affected version or image digest, deployment profile, impact, prerequisites, sanitized reproduction, and any proposed mitigation. Remove credentials, prompts, responses, personal data, customer identifiers, private endpoint names, and proprietary model output.

The maintainer will acknowledge and triage reports on a best-effort basis, coordinate a fix and advisory when warranted, and credit reporters who request attribution. Timing depends on severity and maintainer capacity; this policy creates no contractual commitment.

## Operator responsibilities

Operators must use TLS at every untrusted boundary, least-privilege workload identity, OIDC for administration and sensitive tenants, scoped/expiring API keys only for approved non-sensitive workloads, encrypted storage, outbound allow-lists, and metadata-only logs. Rotate credentials after suspected exposure and follow [the incident runbook](docs/operations/incident-response.md).

The project threat model and known alpha limitations are documented in [docs/threat-model.md](docs/threat-model.md). Security claims require executed evidence; configuration availability is not proof of secure operation.
