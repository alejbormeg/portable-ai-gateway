<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Results — Portable AI Gateway productization

Status: `inconclusive`

On 2026-09-01, the integrated local source gate passed 135 Python tests plus static configuration, deployment, supply-chain-contract and air-gap-format checks. The runtime now enforces one monotonic pre-response deadline across ordered provider attempts, renews short ownership-bound concurrency leases, and uses a bounded PostgreSQL connection pool with idle health checks; the release design binds both OCI platforms, a reproducibility record, exact scanner reports, a hashed external harness and source archive, standalone PostgreSQL/Valkey archives, and a digest-specific independent review with protected-environment approval history. Publication is draft-first and resumes only when existing assets are byte-identical.

No dependency/image download, container start, image build, cloud/cluster deployment, external provider call, cryptographic signing or benchmark was executed. This is therefore a locally source-tested alpha baseline, not cross-platform, production, performance, legal or artifact certification.

## Executed local source gate

The exact commands, versions, counts, checksums and limitations are captured in [local-validation.md](evidence/local-validation.md). Local synthetic coverage includes:

- data/control API behavior, mock inference, native Responses, streaming and fallback before the first byte;
- tenant/OIDC/scope/key lifecycle negatives, malformed JWKS, metadata-only audit and no access-log body/query leakage;
- atomic state/audit writes, coherent config versions, GitOps reconcile and controlled dependency failures;
- replaceable composition/SPIs and configuration-selected mock, OpenAI-compatible and Vertex adapters;
- conservative tools/structured-output/multimodal capability filtering for initial and fallback targets;
- release evidence, multiarch signing/digest gates, draft publication invariants and relocatable standalone air-gap structure.

These checks are not substitutes for PostgreSQL/Valkey integration, real IdP/Vertex/BYO interoperability, OCI build/scanning/signing, deployed platform conformance or load evidence.

## Metrics

| Metric | Threshold | Observed | Status |
| --- | --- | --- | --- |
| Mandatory functional conformance | 100% | 135/135 local tests; required real-service/platform matrix absent | Partial / inconclusive |
| Negative auth/tenant isolation | 100%; zero disclosure | Local negative scenarios pass; production IdP/adversarial runs absent | Partial / inconclusive |
| Prohibited content/secret events | 0 | Audit/access-log/source canaries pass; complete logs/traces/support-bundle scan absent | Partial / inconclusive |
| Provider switch without core change | Three adapters by configuration | Three adapters and six extension families locally exercised; external customer build absent | Partial / inconclusive |
| Fallback safety | Before first byte; zero policy violations | Local operation/stream/capability/residency checks, shared pre-response deadline and fail-closed lease-loss behavior pass; real-provider/Valkey/platform-residency runs absent | Partial / inconclusive |
| Completed request rate | >=50 req/s, >=99% success | Not executed | Inconclusive |
| Simultaneous streams | >=100, >=99% completion | Not executed | Inconclusive |
| Gateway-added latency | Paired p95 <=50 ms | Not executed | Inconclusive |
| Cross-platform digest | One digest passes every required target | Static Compose/Helm gates only; no image digest | Inconclusive |
| Offline vendor dependency | Zero calls | Relocatable three-image format/checksum smoke; no runtime execution | Inconclusive |
| License feature parity | Identical source/artifact | One source/no license paths; no real artifact comparison | Inconclusive |
| Reproducible build | Two clean builds match both platform digests | Contract/test implemented; no builds executed | Inconclusive |
| Synthetic cost / budget | Approved budget, zero violations | No measurement; external cost was EUR 0 | Inconclusive |

## Closed implementation gaps

- First-party non-GitOps bootstrap/reconcile and admin mutation/audit writes are atomic; commit failures roll back and reads/failures report a coherent configuration version.
- Request capabilities are derived once and consistently applied to initial and fallback targets across tools, structured output and multimodal inputs.
- Stateful production CLI operations require OIDC administrator identity and are metadata-audited.
- Uvicorn access logging is disabled in both HTTP modes to avoid query-string token leakage.
- Malformed JWKS entries fail during configuration and have a runtime 401 defense.
- Candidate/release evidence covers amd64 and arm64 gateway SBOM/scans/signatures; the official air-gap input freezes gateway, PostgreSQL and Valkey.
- The external harness executable and corresponding source archive are version/revision/hash-bound; raw reports and reproducible-build results are hash-bound and revalidated at dossier/release time.
- The release review must match source commit, exact image digest, protected-environment policy and an archived, exact approval decision from a reviewer distinct from the workflow initiator/re-run actor.
- `PAG_UPSTREAM_TIMEOUT_SECONDS` is one monotonic budget across ordered completion attempts and stream preparation; it does not permit fallback after the first byte.
- Concurrency leases have a short ownership-bound renewal; an unconfirmed renewal closes a live stream before its next emitted chunk.
- PostgreSQL readiness safely replaces one failed idle DB-API connection and never retries an in-flight transaction.
- PostgreSQL uses a bounded 1–5 connection pg8000 pool per process; exhausted acquisition fails instead of creating unbounded connections.
- Air-gap Compose is image-only, relocatable and structurally requires gateway/PostgreSQL/Valkey for the official standalone bundle.
- GitHub release publication uses a resumable draft, verifies every existing asset byte-for-byte and never clobbers a different asset.

## Remaining release blockers

- Create the GitHub repository/remote, replace repository/legal/contact placeholders and produce the initial clean commit.
- Complete professional review of title/copyright, employer/author rights, AGPL/commercial terms, commercial contract, trademarks and third-party compatibility.
- Generate and review hashed Python/build/test locks, Terraform provider locks, real base/dependency digests and exact third-party notices.
- Build the real multiarch candidate, generate/scan/sign/attest its exact artifacts and execute a second clean reproducible build.
- Run Compose with PostgreSQL/Valkey, production OIDC/JWKS, Vertex and BYO integration.
- Execute the same digest on Cloud Run, Kubernetes/kind, OpenShift restricted and disconnected runtime, including failure, isolation, backup/restore and zero-egress scenarios.
- Execute 50 req/s, 100 streams, <=50 ms gateway-added p95 and approved quality/cost repetitions.
- Configure and execute the protected independent-review environment, bind the human approval history to the final commit/digest, and classify the full PoC as `supported` before tagging.

Known pre-production engineering work remains recorded in `ROADMAP.md`: real PostgreSQL pool validation, real-Valkey long-stream lease testing, and real-provider measurement of the global deadline.

## Conclusion

The result remains `inconclusive`, not rejected: no reproducible failure exists in the executed local scope, while mandatory artifact, platform, performance, legal and operational evidence does not yet exist. `v0.1.0-alpha` may be described only as an implemented, locally source-tested baseline with fail-closed publication gates. The independent review in [independent-review.md](evidence/independent-review.md) remains no-go until a commit and image digest are available.
