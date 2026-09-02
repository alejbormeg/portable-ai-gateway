<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Local source-validation evidence

Result: `pass within executed scope`; full PoC classification: `inconclusive`.

## CI remediation validation — 2026-09-02

The first public GitHub Actions run exposed three reproducible CI defects. They were repaired locally and the following clean-environment checks passed:

- `python -m pip install --editable '.[dev,production]'` in a temporary Python 3.12 virtual environment, followed by `python -m pytest --cov=portable_ai_gateway --cov-report=term-missing --cov-report=xml`: **136 passed**. The `dev` extra now explicitly contains `pytest-asyncio`; the Vertex/production extra contains `google-auth[requests]`, required by Google ADC's requests transport.
- Terraform `1.10.5` downloaded from HashiCorp, checked against its published SHA-256 manifest, then `terraform fmt -check -recursive`, `terraform init -backend=false -input=false`, and `terraform validate`: **passed**. The selected signed provider is recorded in `infra/terraform/gcp/.terraform.lock.hcl`.
- The Security workflow was reproduced with its pinned Bandit tool: SAST passed after scoped suppressions for container binding and internally generated SQL placeholders. A clean production-only dependency audit passed with `cryptography 50.0.1`; the committed secret scan found zero findings, with only documented structural/chart references allowlisted.
- The GCP module no longer derives a resource `for_each` decision from the sensitive JWKS secret; because the production profile already requires a static non-empty JWKS, its secret, bindings and Cloud Run environment references are unconditional.
- A Compose execution was authorized and attempted, but the host denied access to `/var/run/docker.sock`; `sudo -n docker version` also required an interactive password. No container, volume, network, image or cloud resource was created by this attempt.

The attempted CI run had also indicated that the data plane became unhealthy. The advisory transaction lock added around PostgreSQL schema initialization removes the likely simultaneous data/control-plane catalog-initialization race, but this remains unverified until a host with usable Docker access runs the real-service Compose gate.

## GitHub Compose diagnosis — 2026-09-02

The user supplied the complete GitHub Actions log archive for commit
`167a3fd5a89f8016ec1f3b447662adf1afb6786a`; its SHA-256 is
`efce0336b3593758b105eaf2c3efb9bc473916dda26edba249624cfb9291207f`.
The evidence shows:

- the Python job passed **137/137** tests with 80% measured line/branch coverage;
- PostgreSQL 17.6, Valkey 8.0.3 and both gateway processes started, and all four container health checks became healthy;
- the pg8000 cursor correction therefore closed the prior startup failure;
- the public contract failed before its first assertion because Docker created no host port mapping for either gateway, despite the requested Compose `ports` entries;
- both gateway processes were listening internally on `0.0.0.0:8080` and `0.0.0.0:8081`, while `docker compose ps` reported an empty `PORTS` column.

The failing topology attached the gateways exclusively to an `internal` bridge.
Current Docker Engine does not publish host ports for that topology. The
remediation attaches only the two gateway services to an additional normal
`host-access` bridge, keeps PostgreSQL and Valkey exclusively on the internal
backend, and adds an executable topology regression test.

A follow-up GitHub Compose log for commit
`44f75cb0b27ef112aeea758d5db42e843831035f` (supplied-text SHA-256
`e0dc130afe9293b00eac6eb9eb77d509c5d3e37ba9f00e347d96ba8204b1f701`)
proves both loopback mappings, health, readiness, model listing, deterministic
Chat Completions, `Cache-Control: no-store`, and native Responses. Its final
negative-admin assertion failed because anonymous development authentication
was also evaluated by the control plane, producing an OIDC denial instead of
the expected missing-credential `401`. The corrected source now makes anonymous
development identity data-plane-only, preserves the authentication SPI, passes
**142/142** local tests, and still requires one final GitHub rerun. No local
container was started while applying this remediation.

## Execution identity

| Field | Value |
| --- | --- |
| UTC date | 2026-09-01 |
| Repository state | New local Git worktree; no commit and no remote |
| Product version | `0.1.0-alpha` |
| Host | Linux x86_64, kernel `6.12.0-211.46.1.el10_2.x86_64` |
| Python | `3.12.13` |
| pytest | `8.4.1` |
| Docker Compose | `v5.4.0` (configuration resolution only) |
| Helm | `v3.19.4` (lint/package/template only) |

The Python executable and installed packages came from a pre-existing local proof-of-concept virtual environment outside this repository. Its path is intentionally omitted from publication evidence so the clean product does not retain prototype identifiers. No dependency was installed, upgraded or downloaded, and no source or history was imported from that proof of concept. In the transcript, `PREEXISTING_POC_PYTHON` denotes that interpreter.

## Executed source and contract gate

All inputs and outputs were synthetic. Representative commands executed from the product root were:

```sh
env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src timeout 180 \
  "$PREEXISTING_POC_PYTHON" -m pytest -q -p no:cacheprovider
# exit 0; 135/135 collected tests passed

env PYTHONDONTWRITEBYTECODE=1 \
  "$PREEXISTING_POC_PYTHON" -m compileall -q src tests tools
# exit 0

find scripts -type f -name '*.sh' -exec bash -n '{}' ';'
# exit 0
```

The 135-test suite includes:

- data/control API contracts, Chat Completions, native Responses passthrough, streaming and before-first-byte fallback;
- OIDC/scopes, tenant isolation, sensitive tenants, key expiry/rotation/revocation, JWKS fail-closed behavior and audit privacy;
- atomic first-party mutation/audit transactions, rollback on commit failure and coherent configuration versions;
- six extension families, provider factory/composition, GitOps reconciliation and conservative nested capability detection;
- mock, OpenAI-compatible and Vertex request behavior with mocked transports/credentials;
- one monotonic deadline shared across retryable provider attempts for both completion and pre-first-byte stream preparation;
- ownership-bound renewal of the short concurrency lease and fail-closed behavior after a synthetic lease loss;
- one safe PostgreSQL readiness reconnect outside transactions and refusal to reconnect during an active transaction;
- bounded pg8000-pool configuration, health-checked idle replacement, exhaustion timeout, return and clean close using synthetic DB-API doubles;
- air-gap bundle structure/relocatability and rejection of a non-standalone bundle;
- exact release-evidence acceptance plus negative arm64-scan, report-hash, budget, clean-composition and `kind`-identity cases;
- static workflow invariants for build-once promotion, digest-bound independent review with protected-environment approval history, symmetric multiarch signing and draft-first byte-safe publication/retry.

`pytest-cov` was not installed in the pre-existing environment, so no coverage percentage is claimed and no plugin was downloaded.

## Configuration and deployment-static checks

The development, on-prem and deterministic benchmark `GatewayConfiguration` documents passed application-level validation with development-only settings. The raw GCP template failed closed on `YOUR_GCP_PROJECT`; an in-memory copy with `portable-gateway-test` passed, proving the template must be hydrated rather than accepted accidentally.

The following completed without starting a container or contacting a registry:

- base Compose and provider-egress override resolution;
- image-only air-gap Compose and its BYO override with synthetic digest references;
- three Helm lint profiles (default, OpenShift, and production with synthetic digest/OIDC values); production values without those mandatory inputs fail closed;
- parsing of every workflow YAML plus the product configuration documents;
- Bash syntax for every script;
- 40 remote GitHub Action references pinned to full 40-hex commits; two local reusable-workflow references;
- 47 local Markdown links with zero missing targets;
- SPDX headers on 81 code/config/workflow/template files;
- source regex scans with no obvious private key/cloud token, prototype path, license-key, activation, phone-home, commercial-mode or enterprise-feature path.

Terraform/OpenTofu, `actionlint`, `shellcheck`, `cosign` and `pytest-cov` were unavailable; none was downloaded. Terraform received structural review only.

This statement applies to the 2026-09-01 source gate. The separate 2026-09-02 remediation validation above downloaded `pytest-cov` and Terraform 1.10.5 into temporary local paths under explicit approval; no cloud credentials, container images, artifact publication or billable resource was used.

## Relocatable air-gap format smoke

A fresh temporary directory used opaque local stand-ins plus the synthetic SPDX fixture to create a three-record bundle (`gateway`, `postgres`, `valkey`). These checks passed:

- `verify-airgap-bundle.sh --standalone --checksums-only`;
- extracted base Compose resolution;
- extracted BYO-provider override resolution;
- absence of any `build:` fallback and use of sibling `../config/...` paths;
- rejection of a gateway-only bundle when `--standalone` is required.

The temporary directory was removed by exact path. This proves archive layout, canonical checksums, structural hardening and relocatable Compose resolution only. It does not prove genuine image contents, SPDX completeness, Sigstore authenticity, corresponding-source match, offline runtime or zero egress.

## Release gates exercised locally

- The release evidence validator accepted complete synthetic Compose, `kind` Kubernetes and air-gap dossiers and rejected a failed arm64 vulnerability result, a tampered raw-report hash, a budget violation, first-party composition changes and a generic Kubernetes identity.
- The candidate/release workflow contracts parse as YAML and require an external protected-environment approval record from a reviewer distinct from the initiator/re-run actor before accepting a signed independent-review record.
- The final release gate rejected `LEGAL_RELEASE_APPROVED=false` as intended.
- The candidate gate rejected unresolved repository/image placeholders in the packaged chart as intended.
- Workflow YAML and embedded shell parse checks passed, but no GitHub Actions job, candidate build, registry promotion, signature, attestation or release upload ran.

## Input checksums at evidence capture

```text
4b7030c5c7a1e2888116cd24aa060c872e12c3bbe22991e9d971bc1ff8e47dcd  deploy/config/development.yaml
d3e7fe148002ae0eccc1cc8b142e95a23c45c2103c5bbad99f5188a05edfe780  pyproject.toml
60ed669ba94172c5f8a7e60859a7ebba4157ddf66ae713b7ea60d3dd258479c2  VERSION
50c0a8cdf5d17c38268aa1d532406bccc26d40364f7adda23a2e1121163d437d  scripts/validate-release-evidence.sh
c679e00ede34fb4231ce6bf372d0a9b3dd66f2f4653b61a45f75754bc4f5f951  .github/workflows/candidate-validation.yml
68b64951d28872dc8d6775869c1b507b08c43335164d64f9a6ab747ce29c7287  .github/workflows/release.yml
426264d1d0ed4f7c7d249b58bc88184d131a61db0e5fa94fe93afc7d1e223104  tests/test_release_workflow_contract.py
0332e21733e810ec72460c9c102416b48c803c5e6b66fa0d8120ee377da0bdd1  src/portable_ai_gateway/routing.py
3c9e71650404aa45c85036c1d4aca55607309d4361ca95dfff1c73969242438d  src/portable_ai_gateway/apps.py
2864a5a670023b30d49f9f4ae4e8df381d4ced096514c70c1010b16898695d20  tests/test_capability_detection.py
d2fb652ee60ff65fce153772302871457f41038f5d74cab84af9f285e1f7ba14  src/portable_ai_gateway/cache.py
f8076287cda54e31b41b076f05eea248694371bf37366ad9f3da9c496abca0a4  src/portable_ai_gateway/service.py
022e6a43a79555ee1aade5cfe5521ac296013c8b05a6ab6c2b4a311828fe9d26  tests/test_runtime_extensions.py
66cc79362eed050913b9a2263187c0b5b774d35c17dccce699367d7d3df806e4  tests/test_store_security.py
ddaf3495f42d9b1ec0f8f38d809e20afb3620e205b0427104c0b874d289cd5fc  src/portable_ai_gateway/config.py
82fd1328a5a815d196331b238d80e707bfe9c48ebd94b82fcd75aeba8ef8ae6b  src/portable_ai_gateway/store.py
fabd5cb4a5ac2c6419c2825345974b3634eba56b39f20e4de37d27d6aa45030c  tests/test_config.py
360c3c9223eda677ea209959cb57b2ff82e05f3413510c81da87e1c2ebedd029  src/portable_ai_gateway/cli.py
```

## What this evidence does not prove

- No Docker image was built and no Compose service was started; PostgreSQL and Valkey were not integration-tested because the host denied Docker socket access. PostgreSQL reconnection/pool behavior uses synthetic DB-API stand-ins and is not evidence of real pg8000, server or pool behavior.
- No production IdP/JWKS, Vertex/BYO endpoint, Cloud Run, Kubernetes, kind or OpenShift environment was contacted.
- No real OCI digest, dependency lock, SBOM, vulnerability/license scan, signature, provenance or reproducible second build exists.
- No load, 100-stream, paired-latency, 100-run quality or synthetic-cost measurement ran. Deadline and lease-renewal tests use synthetic in-process delays/cache behavior and are not evidence of provider or Valkey behavior in production.
- No complete runtime scan of logs, traces and support bundles ran.
- No legal, copyright, trademark or dependency-license conclusion is inferred from technical tests.

## External actions and cleanup

No credentials, container images, resources, deployments or publications were used. The 2026-09-02 remediation validation downloaded Python packages, Terraform and one Terraform provider into local temporary/cache paths under explicit approval; it created no billable resource and recorded external cost remains EUR 0. Only project-local ignored Python caches may remain. The original proof of concept and existing GCP resources remained untouched.
