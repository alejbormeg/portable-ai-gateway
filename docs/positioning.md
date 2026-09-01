<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Product positioning

Portable AI Gateway is positioned as a **customer-owned, fully inspectable gateway with enterprise controls in the open codebase**, not merely as “an open-source alternative to a closed subscription.” That latter claim would be inaccurate for several existing gateways.

LiteLLM, for example, publishes a self-hosted open-source gateway and licenses code outside its `enterprise/` directory under MIT, while maintaining separately licensed enterprise functionality. Its public pricing describes both self-hosted open-source and paid enterprise offerings. Refer to its current [repository license](https://github.com/BerriAI/litellm/blob/main/LICENSE), [enterprise license](https://github.com/BerriAI/litellm/blob/main/enterprise/LICENSE.md), and [pricing](https://www.litellm.ai/pricing) before making any comparison; those external terms may change.

## Verifiable differentiation

For Portable AI Gateway, the differentiating contract is:

- data plane, control plane, OIDC, scoped keys, tenant isolation, policy, quota, routing, audit, deployment assets, and extension contracts are first-party open-source code;
- the commercial option covers the same commit and artifact, with different legal permissions rather than additional features;
- no license server, phone-home, hosted vendor control plane, commercial feature flag, or proprietary configuration database is required;
- customers can build from source, pin one image digest across environments, keep ownership of configuration and operational data, and replace documented adapters;
- corresponding source is discoverable from production responses through a configured source link.

Each statement must remain backed by source, artifact, and offline tests. If a future feature is proprietary or service-dependent, update this positioning instead of preserving an outdated “all open” claim.

## Honest trade-offs

- `AGPL-3.0-only` is stronger copyleft than permissive licenses such as MIT or Apache-2.0. Some organizations will prefer a commercial license after legal review.
- This project is an alpha with a small initial provider set. It does not claim broader provider coverage, maturity, support capacity, or operational history than established alternatives.
- “Portable” is an evidence-based target, not a guarantee that every Kubernetes, OpenShift, GCP, identity, database, cache, or model configuration behaves identically.
- “100% customizable” applies to first-party gateway code and delivered assets. It does not grant modification rights over third-party platforms, managed services, or model providers.

Marketing, release notes, and sales material must use these bounded statements and must not describe competitors as closed, SaaS-only, or subscription-only without current primary evidence.
