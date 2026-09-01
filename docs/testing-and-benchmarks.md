<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Testing and benchmarks

A manifest, code path, or green unit test is not evidence that the product works on every target platform. Results must name the exact commit, image digest, environment, data, command, repetitions, deviations, and raw evidence location.

## Required test layers

- Unit: schemas, auth claims, key hashing/revocation, policy, capability/residency routing, retry classification, audit allow-list, and secret redaction.
- Integration: PostgreSQL and Valkey lifecycle, atomic configuration, quota/concurrency, provider adapters, streaming cancellation, and dependency failure.
- API conformance: models, Chat Completions, native Responses, stable errors, request IDs, SSE framing, tool calls, structured output, and unsupported capability rejection.
- Security: no-auth/invalid-auth, cross-tenant negative cases, GitOps mutation rejection, SSRF endpoints, oversized bodies, log/audit canaries, and dependency/image scanning.
- Deployment: Compose, Cloud Run, Kubernetes, and OpenShift restricted using the same digest.
- Offline: install and execute the air-gap bundle with outbound traffic denied and no vendor dependency.
- Supply chain: reproducible source build, SBOM, signature, provenance, license inventory, and AGPL source-link verification.

## Performance acceptance plan

The alpha target is **50 completed requests/second**, **100 simultaneous streams**, and **gateway-added p95 latency no greater than 50 ms** in the documented reference environment.

Use a deterministic local HTTP mock as the baseline and the gateway routed to that same mock as the variant. Keep host, network path, payload corpus, concurrency, warm-up, connection reuse, logging, and measurement window constant. Report baseline and variant percentiles separately; gateway overhead is the paired difference, not total model latency.

Run at least three measured repetitions after warm-up. Reject a run with uncontrolled host saturation, background load, throttling, errors outside the planned fault set, or mismatched payloads. Report success/error rate, p50/p95/p99 latency, achieved throughput, stream duration/time-to-first-byte, CPU, memory, open connections, PostgreSQL/cache load, and dropped audit events.

Passing requires every functional/security prerequisite plus all three performance thresholds in every accepted repetition. A result that cannot isolate gateway overhead or does not represent both normal and streaming load is `inconclusive`, not a pass.

## PostgreSQL pool scenario (prepared, not executed)

The Compose profile is the first approved environment for pool validation. Use only synthetic data and an approved local image/dependency set. Record the exact image digests, PostgreSQL version, pool settings, active data/control-plane replica count, command output, database connection count, and cleanup state. Exercise five concurrent borrowed connections per process, a sixth bounded acquisition, readiness after an idle connection is broken, PostgreSQL restart/recovery, a rollback during a mutation, and verification that the mutation was not duplicated. Do not run this scenario until container execution has been explicitly approved; no local source test is evidence for pg8000 or PostgreSQL behavior.

### Source-only reference harness

The repository includes a deterministic loopback provider and a paired asynchronous load driver. They are source-validation tools, not installed runtime modules. Start three processes from an approved environment with the project dependencies already installed:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
# terminal 1: direct HTTP baseline, held streams make concurrency observable
python tools/deterministic_provider.py --host 127.0.0.1 --port 9000 --stream-hold-seconds 1.5

# terminal 2: gateway variant routed to that exact provider
PAG_APP_ENV=development \
PAG_DATABASE_URL=memory:// \
PAG_CACHE_URL=memory:// \
PAG_CONFIG_PATH=deploy/config/benchmark.yaml \
PAG_ALLOW_ANONYMOUS_DEV=true \
python -m portable_ai_gateway data-plane --host 127.0.0.1 --port 8080

# terminal 3: 3 repetitions; output directory is ignored by Git
python tools/benchmark.py --output benchmark-results/local.json
```

The driver alternates baseline/gateway phase ordering, schedules 60 offered requests per second to measure at least 50 completed requests per second, opens 100 streams against both paths, requires 99% success, and computes paired p95 overhead. It returns `supported`, `rejected`, or `inconclusive`; an invalid baseline is always inconclusive. It never prints the optional bearer value. Non-loopback targets are rejected unless the operator explicitly supplies `--allow-non-loopback`.

Before accepting a run, record host CPU/memory/network state and inspect the JSON for all three repetitions. The driver does not collect host/container resource metrics or prove that an environment was uncontended; attach those measurements separately. Do not publish internal endpoint URLs from a platform run without sanitization.

The 99.9% availability figure is not tested by this benchmark. It requires a separately approved SLO definition, error budget, measurement window, production-representative failure model, and operational evidence.

## Evidence status

The formal hypothesis and current execution state are recorded under [`.ai-ra/pocs/portable-ai-gateway-productization`](../.ai-ra/pocs/portable-ai-gateway-productization/). Do not copy results from a different PoC or environment into this product's evidence.
