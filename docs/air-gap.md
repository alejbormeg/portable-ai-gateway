<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Air-gap packaging and installation

An air-gap bundle is created only from artifacts already present on the connected build host. The bundle script never pulls an image, contacts a registry, downloads a dependency, or signs an artifact. It requires an OCI/Docker archive, one SPDX SBOM and one Sigstore bundle per image, the corresponding-source archive, the Helm chart, configuration examples, license, and third-party notices.

## Prepare on the connected build host

Build, scan, generate SBOM/provenance, sign, and export each candidate image before semantic acceptance. Pin references by digest and verify that the source archive exactly corresponds to the gateway image. Include PostgreSQL and Valkey images when the disconnected destination will not provide compatible data services.

The candidate workflow freezes the exact `linux/amd64` gateway archive, both gateway-platform SBOMs, source and chart alongside the multi-platform OCI index. It also freezes reviewed amd64 PostgreSQL and Valkey archives, SBOMs and signatures for a standalone evaluation. The protected air-gap matrix tests those accepted inputs. The tag-triggered release never rebuilds or re-exports an image: it reverifies the candidate archive signatures and assembles the final bundle from those same bytes; only the top-level release bundle receives its release-workflow signature. An ARM or other disconnected target requires separately frozen, SBOMed, signed and runtime-validated dependency archives plus explicit manifest entries; an amd64 bundle is never evidence for another architecture.

Package only local files:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
scripts/create-airgap-bundle.sh \
  --version 0.1.0-alpha \
  --image 'gateway|ghcr.io/OWNER/portable-ai-gateway@sha256:REPLACE|/approved/gateway.oci.tar' \
  --sbom 'gateway|/approved/gateway.spdx.json' \
  --signature 'gateway|/approved/gateway.sigstore.json' \
  --source-archive /approved/portable-ai-gateway-source.tar.gz \
  --output artifacts/portable-ai-gateway-airgap-0.1.0-alpha.tar.gz
```

This compact example is a gateway-only custom bundle and therefore assumes PostgreSQL and Valkey are already provisioned in the disconnected environment. A standalone bundle must add one `--image`, `--sbom`, and `--signature` triple for each of `postgres` and `valkey`; the official release workflow always does so. The command invokes local `helm package` and produces deterministic archive metadata plus `SHA256SUMS`. It does not make an unsigned image trusted: release identity and provenance must already be approved.

## Transfer and verify offline

Transfer the bundle, its separately published `.sha256` file, and its external `.sigstore.json` bundle through approved channels. Confirm the checksum through an independently trusted channel. On the disconnected side, first verify the top-level checksum, then require the approved workflow identity for offline Sigstore verification:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
(cd artifacts && sha256sum --check portable-ai-gateway-airgap-0.1.0-alpha.tar.gz.sha256)
export COSIGN_CERTIFICATE_IDENTITY='https://github.com/OWNER/portable-ai-gateway/.github/workflows/release.yml@refs/tags/v0.1.0-alpha'
scripts/verify-airgap-bundle.sh \
  --standalone \
  --signature artifacts/portable-ai-gateway-airgap-0.1.0-alpha.sigstore.json \
  artifacts/portable-ai-gateway-airgap-0.1.0-alpha.tar.gz
```

Checksum and Sigstore verification are offline. `--standalone` requires exactly the `gateway`, `postgres`, and `valkey` image records used by the official bundle. Production verification fails unless `cosign`, the external bundle signature, and an exact `COSIGN_CERTIFICATE_IDENTITY` are present; set `COSIGN_CERTIFICATE_OIDC_ISSUER` if it differs from the documented default. A regular-expression identity is supported only as an explicit migration mechanism and must escape every metacharacter. `--checksums-only` is deliberately limited to format/debug tests and prints that authenticity was not established.

Inspect `manifest/images.tsv`, every SBOM, signature bundle, source archive, legal notice, config, Compose file, and chart. The official release bundle contains `gateway`, `postgres`, and `valkey` records. Import images into an approved internal registry/engine, record the resulting digest, and configure Compose or Helm with those exact references. Importing by tag alone is insufficient.

## Offline runtime requirements

- Provide local PostgreSQL, Valkey/Redis-compatible storage, OIDC/JWKS, DNS, time, registry, audit sink, and the customer model endpoint as applicable.
- Keep `PAG_SOURCE_URL` resolvable to corresponding source for network users inside the disconnected environment; an internal HTTPS source repository is acceptable when users can actually access it.
- Disable external provider routes and enforce egress deny. Vertex is not an offline provider.
- Preload every chart/image/tool needed for install, rollback, backup, restore, and diagnosis.
- Preserve license/notices/source with every redistributed bundle.

## Acceptance

With network egress denied, verify checksums/signatures, install, health/readiness, OIDC, tenant negatives, mock/BYO inference, audit redaction, restart, rollback, backup/restore, and uninstall. Observe zero vendor licensing, telemetry, update, registry, or control-plane calls. Record exact digest/config and packet/network-policy evidence.

Only a format/checksum smoke with opaque local stand-ins has been recorded in the product PoC; no real image, signature or disconnected runtime was validated. See [the evidence record](../.ai-ra/pocs/portable-ai-gateway-productization/results.md).
