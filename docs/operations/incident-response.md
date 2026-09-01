<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Incident response

## Priorities

Protect people and data, contain cross-tenant or credential exposure, preserve trustworthy metadata evidence, restore a known-good service, and communicate through approved channels. Availability must not override tenant isolation, residency, or authentication.

## Response sequence

1. Assign an incident lead and start a private UTC timeline.
2. Classify impact: confidentiality, integrity, availability, licensing/source mismatch, or supply chain.
3. Contain with the narrowest reversible control: revoke one key, disable one provider/route, remove one digest, or stop administrative mutation.
4. Preserve image/config digests, sanitized audit events, platform events, and identity changes. Do not collect prompts/responses “for debugging.”
5. Rotate affected provider credentials, API keys, database secrets, OIDC verification secrets, and signing identities according to scope.
6. Rebuild from reviewed source and pinned dependencies when artifact integrity is uncertain.
7. Recover with negative auth/isolation tests and a synthetic canary before traffic restoration.
8. Complete notification, root cause, corrective controls, and evidence-backed closure.

## High-risk signals

Immediately treat as potential security incidents: a prompt/response or token in logs; one tenant observing another's model/config/audit; an unexpected source URL; unsigned or digest-mismatched artifacts; administrative mutation outside GitOps; fallback outside residency; or a stream being replayed after output began.

Public disclosure follows [SECURITY.md](../../SECURITY.md). This repository provides no regulatory-notification advice; operators must use their applicable legal and organizational process.
