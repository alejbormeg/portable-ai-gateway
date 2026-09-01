<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Release process and publication gates

The target first tag is `v0.1.0-alpha`; `VERSION` contains `0.1.0-alpha`. No accepted image digest or release dossier currently exists, and the recorded PoC/review status is not `supported`. The publication path therefore remains deliberately fail-closed.

## Trust and legal prerequisites

Before public distribution, qualified review must establish code ownership, employer/client/contractor rights, AGPL/commercial dual-licensing authority, commercial agreement, dependency compatibility, privacy/security representations, and product/trademark naming. Resolve every legal/contact/repository placeholder without inventing an identity. Set the repository variable `LEGAL_RELEASE_APPROVED=true` only after the named legal review.

Protect `main` and release tags. Configure required reviewers and restricted deployment branches for these GitHub environments:

- `candidate-build` for the one candidate build;
- `candidate-compose`, `candidate-kubernetes`, `candidate-openshift`, `candidate-gcp`, and `candidate-airgap` for executed external validation;
- `candidate-independent-review` for human review of the exact candidate and matrix evidence;
- `candidate-acceptance` for the signed acceptance dossier;
- `release` for final publication.

`candidate-independent-review` must restrict deployment branches to protected branches, require at least one reviewer, prevent self-review, and disallow administrator bypass under the repository/organization policy. The approving reviewer must inspect the immutable candidate and all five signed result artifacts, then approve the protected job with this exact comment: `PAG-INDEPENDENT-REVIEW: SUPPORTED; critical=0; high=0; non_evaluable=0`. The workflow archives the GitHub approval history and environment policy, requires an approver distinct from both the workflow initiator and re-run actor, and binds their hashes and login(s) into the signed review record. The API-visible policy cannot prove an organization-level administrator-bypass setting; that configuration is an external release audit prerequisite.

Use organization-controlled runners for the validation environments, isolate their credentials per target, retain infrastructure/audit logs, and do not grant pull-request code access to deployment secrets. External contributions remain closed until a dual-license-compatible CLA and maintainer process are public. Code from the earlier PoC may not enter a candidate until provenance and publication rights are independently established.

## Build once, validate, accept, promote

### 1. Create one immutable candidate

Dispatch `.github/workflows/release-candidate.yml` from the exact protected `main` commit and provide an auditable reason. It reuses the complete CI and security workflows before the candidate job receives package/OIDC write permissions. Candidate gates require a clean commit, hash-locked Python closures, a reviewed Terraform provider lock, digest-pinned base images, concrete executable defaults, and a packageable chart.

The job builds and pushes one `linux/amd64` + `linux/arm64` OCI index. It rejects duplicate application descriptors for either architecture, records the raw index bytes, index digest and exact platform digests, then signs the index and platforms. Before semantic validation it also freezes:

- the exact `linux/amd64` gateway archive and its candidate signature;
- SPDX JSON SBOMs for the exact amd64 and arm64 gateway platform digests;
- reviewed amd64 PostgreSQL and Valkey manifests/archives from digest-pinned repository variables `PAG_AIRGAP_POSTGRES_IMAGE` and `PAG_AIRGAP_VALKEY_IMAGE`, with one SPDX SBOM and candidate signature each;
- the source archive from the same commit;
- the packaged Helm chart;
- a signed `candidate.json` containing every path and SHA-256 hash.

Those bytes are uploaded once as `release-candidate-COMMIT`; the workflow summary records its GitHub artifact digest and run ID. The convenience tag `candidate-COMMIT` is not release authority. Every later stage resolves the immutable index by digest and the candidate by run ID plus artifact digest.

Both dependency variables must name an exact `linux/amd64` application manifest (`repository@sha256:...`), not a mutable tag, multi-platform index or attestation manifest. Their selected versions, source, vulnerability policy, licenses and redistribution terms require approval before the variables are configured; the workflow verifies the raw manifest digest and architecture again before admitting the archives.

### 2. Execute the protected portability matrix

Dispatch `.github/workflows/candidate-validation.yml` with the candidate run ID and exact 40-character commit. It checks the candidate workflow identity, run conclusion, source commit, artifact receipt, manifest signature, file hashes, archive signature, registry index bytes and OCI signature before scheduling validation.

Each target uses a dedicated self-hosted runner label `pag-TARGET`, the protected executable `/opt/portable-ai-gateway-validation/run`, and its corresponding source archive at `/opt/portable-ai-gateway-validation/source.tar.gz`. Configure their reviewed SHA-256 values as environment variables `PAG_VALIDATION_HARNESS_SHA256` and `PAG_VALIDATION_HARNESS_SOURCE_SHA256` in every `candidate-TARGET` GitHub environment. Target credentials and runner provisioning remain outside the repository, but every signed result records the harness contract, name, version, source revision, executable bytes and source archive. The workflow independently hashes both files, requires equality with those protected allowlists and rejects a different result value. The public `scripts/validate-release-evidence.sh` is the authoritative, versioned output validator reused unchanged by the matrix, dossier and release jobs.

The harness receives these immutable inputs:

- contract `portable-ai-gateway.dev/release-validation/v2` and target name;
- repository, source commit and candidate artifact digest;
- OCI repository plus accepted multi-platform index digest;
- candidate manifest, chart, source archive, both gateway SBOMs, the amd64 gateway archive/signature, and the frozen PostgreSQL/Valkey archives, SBOMs and signatures;
- an output directory for `result.json` and raw evidence.

For every target, `result.json` must bind the repository, commit, index digest, candidate manifest hash, candidate artifact digest, hashes of the archive/chart/SBOM/source, platform identity and the exact harness executable/source bytes. It must report `status: supported` plus executed test, deployment, security-negative and audit/privacy acceptance. Raw evidence is mandatory. The Compose result additionally proves at least 50 completed requests/s, 100 simultaneous streams and no more than 50 ms gateway-added p95 latency across three repetitions; a clean-checkout customer composition with mock, OpenAI-compatible and Vertex providers; 100 deterministic mock operations with quality, resource and approved synthetic-cost reports; no raw-content telemetry; and zero critical/high/non-evaluable traceability findings. It is also the canonical exact-artifact security record: both gateway platform digests require successful vulnerability, license, SBOM-integrity and provenance records. Each record names scanner/version, vulnerability-database timestamp, applied policy and a report path/hash. Scanning source or an unbound mutable tag is insufficient. Compose also records an independent clean rebuild from the accepted source and requires the rebuilt amd64/arm64 platform manifest digests to equal the candidate; at least two builds are compared. Kubernetes validation specifically identifies a `kind` cluster and proves a provider swap without domain/application changes. The air-gap result binds equivalent reports for the exact PostgreSQL/Valkey archives, proves a standalone Compose run, zero runtime egress and offline signature verification. Each result and its raw-file checksum inventory is signed by the validation workflow.

The five required targets are Compose, Kubernetes, OpenShift restricted, GCP Cloud Run and air gap. A skipped, simulated, `inconclusive`, expired, unsigned or mismatched target blocks acceptance. The same multi-platform index digest is passed to the four connected deployment targets; the air-gap path exercises the exact accepted amd64 gateway, PostgreSQL and Valkey archives. Arm64 is supply-chain scanned and SBOMed before publication; it is not runtime-certified unless an arm64 execution target is later added to the signed matrix.

### 3. Create the immutable acceptance dossier

After all matrix jobs and the protected `candidate-independent-review` job succeed, the protected `candidate-acceptance` job downloads only artifacts from that validation run, reverifies every checksum/signature and creates a signed dossier with schema `portable-ai-gateway.dev/release-dossier/v1`. The independent record is signed by the validation workflow after it verifies the protected-environment policy and the exact GitHub approval history. The dossier binds:

- source commit and tree;
- candidate version, run ID, artifact digest and manifest hash;
- OCI repository and multi-platform index digest;
- validation workflow commit, run/attempt and the five signed evidence hashes.
- signed independent-review record, signature, artifact digest, approval-history hash and reviewer decision.

The artifact `accepted-release-COMMIT` is immutable in GitHub Actions and retained for 90 days. Create the release tag before it expires. Acceptance does not replace the independent architecture/security evidence classification or legal approval; both remain final release gates.

### 4. Tag and promote without rebuilding

Copy the exact six trailers printed by the acceptance job into an annotated (preferably signed) tag message:

```text
Portable AI Gateway VERSION

Release-Source-Commit: 40_HEX_COMMIT
Release-Image-Digest: sha256:64_HEX_INDEX_DIGEST
Release-Candidate-Run: CANDIDATE_RUN_ID
Release-Dossier-Run: DOSSIER_RUN_ID
Release-Dossier-SHA256: sha256:64_HEX_DOSSIER_HASH
Release-Dossier-Artifact-Digest: sha256:64_HEX_ARTIFACT_HASH
```

Point `vVERSION` at `Release-Source-Commit`. Do not hand-edit a trailer or derive it from a mutable tag. The release workflow rejects lightweight tags, duplicates, unknown `Release-*` trailers, commits outside `main`, a version mismatch, expired artifacts, or any mismatch among the tag, GitHub workflow receipts, signed candidate, signed dossier, raw evidence, source tree and registry bytes.

`.github/workflows/release.yml` contains no image build. After all gates pass, it reads the accepted raw OCI index by digest, verifies its SHA-256 and byte equality, then assigns the version tag to that existing index. A post-promotion check requires the version tag to resolve to exactly the accepted digest and raw index bytes; an existing version tag may be reused only when already identical and is never overwritten with different bytes.

The release also reuses the accepted source archive, chart, both gateway SBOMs and the three amd64 archives. Candidate archive signatures remain the signed authority for their frozen bytes; top-level release-asset signatures are reused only after verification against the exact payload and release workflow identity, which makes safe retries byte-stable. It applies release signatures to the index and both gateway platform manifests, applies both platform SBOM attestations, publishes the accepted chart only when an existing version is byte-identical, assembles and verifies the standalone air-gap bundle, publishes the signed dossier archive, and emits a signed final receipt that again binds commit, image digest, both gateway SBOMs, dependencies, dossier, independent review, chart and release assets. It does not run `docker build`, repackage the chart, regenerate an SBOM, re-export an image, or create a new OCI index after semantic acceptance.

GitHub release publication is draft-first and resumable. On a retry, every existing asset is downloaded and byte-compared before reuse; missing assets may be appended, but a different byte sequence or an unexpected extra asset fails the job and is never overwritten with `--clobber`. The draft becomes a public alpha pre-release only after the exact asset set passes verification. Product versions use OCI-compatible SemVer and therefore may contain a prerelease suffix such as `-alpha`, but not `+build` metadata.

## Source and dependency contract

Generate locks only in a reviewed, network-enabled dependency-resolution job; do not hand-invent versions or hashes. `requirements.lock` contains the complete `production` runtime closure, `requirements-build.lock` the PEP 517/Hatchling build closure, and `requirements-test.lock` the `dev` test closure. Every admitted artifact/platform is exact and hashed. Resolve the files together, then review vulnerabilities, provenance and licenses. Generate and review `.terraform.lock.hcl` for every supported provider platform. The release Dockerfile must use digest-pinned bases.

`PAG_SOURCE_URL` must resolve to complete corresponding source for the accepted image. The published source archive hash, candidate manifest, dossier and final receipt must agree. Python packaging may canonicalize the product label `0.1.0-alpha` as PEP 440 `0.1.0a0`; OCI/chart/release versions remain the product SemVer.

## Publication and post-release

Publish the annotated source tag, exact GHCR image digest, Helm chart OCI digest, source archive, SBOM, signatures/attestations, air-gap bundle/checksum, candidate manifest, acceptance dossier and final release receipt. Tags are convenience references; deployment configuration remains pinned by digest.

Release notes must say `alpha`, `not production-ready`, `no SLA`, enumerate limitations, and distinguish executed platforms from design-only assets. The AGPL and commercial offerings use identical code and artifacts.

After publication, verify every source link, digest and signature from a clean environment; preserve external harness evidence and cleanup records; enable private vulnerability reporting; and monitor best-effort issue intake. Revoke a compromised artifact through an advisory and a new immutable version—never replace bytes behind an existing version.
