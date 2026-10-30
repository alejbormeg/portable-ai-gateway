<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Licensing model

Portable AI Gateway uses a dual-license model over one codebase and one feature set:

1. **Open-source option:** GNU Affero General Public License v3.0 only (`AGPL-3.0-only`). The complete terms are in [LICENSE](LICENSE).
2. **Commercial option:** a separately signed license from the copyright holder for organizations that need rights incompatible with the AGPL, such as confidential modified network deployments, proprietary redistribution, OEM embedding, or closed hosted-service use.

There is no “community” versus “enterprise” binary, feature flag, license key, hosted control-plane dependency, or phone-home check. A commercial customer receives different legal permissions, not different software capabilities.

## What the AGPL option means

The AGPL permits use, study, modification, and redistribution, including commercial use, subject to its conditions. In particular, section 13 applies when users interact remotely with a modified version over a network: those users must be offered the Corresponding Source of that running version as required by the license.

The following operational rules are project policy, not a substitute for reading the license:

- Preserve copyright, license, and warranty notices.
- Keep the SPDX identifier `AGPL-3.0-only` on first-party source files.
- Provide the complete Corresponding Source when distribution or section 13 requires it, including build and installation material covered by the license.
- Set `PAG_SOURCE_URL` to an absolute HTTPS URL for production deployments. It is exposed by the gateway as a source link. A modified network deployment must point to the source corresponding to the version actually offered to users; leaving an upstream URL on a modified build is not sufficient.
- Do not use project trademarks in a way that implies endorsement; see [TRADEMARKS.md](TRADEMARKS.md).

The runtime check that requires `PAG_SOURCE_URL` in production is a compliance aid, not a guarantee that an operator has met every obligation.

## Commercial option

Commercial terms are negotiated outside this public repository and require a written agreement signed by the copyright holder. The intended commercial model is:

- annual licensing per legal entity for unlimited internal deployments;
- separately negotiated OEM and hosted-service rights;
- optional support, LTS maintenance, CVE management, certified builds, implementation, and training;
- no SLA for `v0.1.0-alpha` unless a signed agreement explicitly says otherwise.

No commercial right is granted merely by describing that model here. Do not create or publish a commercial-license template from this repository without legal review.

## Contributions and relicensing

External pull requests, patches, or documentation contributions are not accepted initially. This protects a clear provenance chain while the sole licensor and commercial agreement are established. Issues, feature requests, and responsible vulnerability disclosures are welcome, but reporters must not paste code they do not own or intend to license.

If code contributions are opened later, the project will first publish a contributor license agreement that explicitly permits the steward to distribute accepted contributions under both AGPL and commercial terms. Until then, maintainers will close external pull requests without merging them.

## Third-party code

The dual-license offer applies only to code for which the project has the necessary rights. Third-party dependencies remain under their respective licenses and are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and release SBOMs. A commercial agreement cannot relicense third-party components.

## Publication gate

The copyright holder is Alejandro Borrego Megías. Licensing, security, and conduct contact: <alejbormeg@gmail.com>. Before the first public release, the release steward must:

- obtain legal approval for the AGPL/commercial dual-license structure and commercial agreement;
- generate and review dependency-license and SBOM evidence for the exact release artifact.

A release containing an unresolved placeholder must fail. This document is informational and is not legal advice.
