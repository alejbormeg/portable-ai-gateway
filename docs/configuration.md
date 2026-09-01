<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Configuration

Portable AI Gateway separates process settings from portable desired state:

- `PAG_*` environment variables configure identity material, storage connections, safety limits, and the path/version of desired state.
- A `GatewayConfiguration` YAML document defines tenants, providers, model aliases, routes, policies, and quotas without embedding secrets.

Unknown YAML fields are rejected. Identifiers start with an alphanumeric character, may contain letters, digits, `.`, `_`, and `-`, and are limited to 128 characters.

Both HTTP process modes inspect the configured file at startup. In GitOps mode they validate and transactionally reconcile it on every start. Outside GitOps, a valid file seeds only an empty database; once configuration exists, PostgreSQL remains authoritative and restarts do not erase imperative changes. A missing file is therefore rejected only in GitOps mode. Every GitOps replica must mount the same reviewed document and reach the same PostgreSQL database; readiness is not a substitute for checking the applied document version.

## Process settings

| Variable | Default | Purpose / production rule |
| --- | --- | --- |
| `PAG_APP_ENV` | `production` | `production`, `development`, or `test`. Production activates strict startup validation. |
| `PAG_DATABASE_URL` | `sqlite:///./portable-ai-gateway.db` | Durable database connection. Production requires PostgreSQL; TCP URLs must set `sslmode=require`, `verify-ca`, or `verify-full`. A local Cloud SQL Unix socket under `/cloudsql/` is exempt. |
| `PAG_POSTGRES_POOL_MIN_SIZE` | `1` | Minimum pg8000 connections warmed per gateway process; valid range 1–20 and never greater than the maximum. |
| `PAG_POSTGRES_POOL_MAX_SIZE` | `5` | Maximum pg8000 connections per gateway process; valid range 1–50. Dimension PostgreSQL/Cloud SQL for data-plane plus control-plane replicas. |
| `PAG_POSTGRES_POOL_ACQUIRE_TIMEOUT_SECONDS` | `2` | Bounded wait for a pool connection; valid range greater than 0 and at most 10 seconds. Exhaustion fails the affected operation rather than creating an unbounded connection. |
| `PAG_CACHE_URL` | unset | Valkey/Redis-compatible connection. Production startup requires `redis://` or `rediss://`. |
| `PAG_CONFIG_PATH` | `/etc/portable-ai-gateway/config.yaml` | Desired-state YAML. Required and authoritative in GitOps; otherwise an optional empty-database seed. |
| `PAG_GITOPS_MODE` | `false` | Reject incompatible administrative mutation when true. |
| `PAG_CONFIG_VERSION` | `unversioned` | Fallback revision identifier. Reconcile records document `metadata.version`; imperative resource changes generate an `imperative@...` revision used by subsequent audit events. |
| `PAG_SOURCE_URL` | unset | Absolute HTTPS URL for corresponding source. Required in production. |
| `PAG_API_KEY_PEPPER` | development-only value | Server-side secret used when hashing API keys; at least 32 characters in production. |
| `PAG_OIDC_ISSUER` | unset | Exact accepted token issuer; required in production. |
| `PAG_OIDC_AUDIENCE` | unset | Exact accepted audience; required in production. |
| `PAG_OIDC_HS256_SECRET` | unset | Static HMAC verification secret for controlled/testing deployments. |
| `PAG_OIDC_JWKS_JSON` | unset | Static JWKS document for RS256. Production requires at least one of this or the HMAC secret. |
| `PAG_CLI_OIDC_TOKEN_FILE` | unset | Preferred secret-file path containing a short-lived administrator JWT for any production CLI command that opens runtime state. |
| `PAG_CLI_OIDC_TOKEN` | unset | Alternative administrator JWT for constrained automation; mutually exclusive with the token file and never accepted as a CLI argument. |
| `PAG_ALLOW_ANONYMOUS_DEV` | `false` | Development convenience; startup rejects it in production. |
| `PAG_MAX_REQUEST_BYTES` | `1048576` | Positive request-size ceiling enforced before parsing provider payloads. |
| `PAG_UPSTREAM_TIMEOUT_SECONDS` | `120` | Positive monotonic budget shared by ordered pre-response provider attempts. It is not an end-to-end SLA and does not bound a stream after its first byte. |
| `PAG_PROVIDER_HOST_ALLOWLIST` | unset | Comma-separated exact provider hostnames. Every configured `base_url` and the resolved default Vertex hostname must appear here in production. |
| `PAG_LOG_LEVEL` | `INFO` | Structured operational log level. Debug logging must still exclude content and secrets. |

Boolean values accept `1/0`, `true/false`, `yes/no`, or `on/off`, case-insensitively. Invalid values fail startup rather than silently using a default.

## Desired-state document

The shipped examples under `deploy/config/` are the canonical starting points. This abbreviated synthetic example contains no credential:

```yaml
# SPDX-License-Identifier: AGPL-3.0-only
api_version: gateway.portable-ai.dev/v1alpha1
kind: GatewayConfiguration
metadata:
  version: example-001
spec:
  tenants:
    - id: default
      name: Synthetic demo tenant
      sensitive: false
  providers:
    - id: mock
      tenant_id: default
      kind: mock
      residency: local
      native_responses: true
      max_response_bytes: 8388608
  models:
    - id: mock-chat
      tenant_id: default
      upstream_model: mock-chat
      capabilities:
        chat: true
        responses: true
        streaming: true
        tools: true
  routes:
    - id: default-mock
      tenant_id: default
      model_id: mock-chat
      targets:
        - provider_id: mock
          residency: local
          priority: 10
          capabilities:
            chat: true
            responses: true
            streaming: true
            tools: true
            structured_output: false
            multimodal: false
  policies: []
  quotas: []
```

Provider credentials use `credential_env` to name an environment variable available to the process. Only the dedicated `PAG_PROVIDER_CREDENTIAL_*` namespace is accepted; never place database, OIDC, gateway-key, or other process secrets in that namespace. The schema and provider factory both reject any other name, so a control-plane administrator cannot select and forward an unrelated gateway environment variable. The YAML never stores the value. Generic upstream URLs must use HTTPS; unencrypted HTTP is accepted only when the parsed hostname is exactly `localhost`, `127.0.0.1`, or `::1`. User info, query strings, and fragments are rejected. Vertex providers require syntax-validated project and location values and rely on workload identity/ADC rather than embedded service-account keys.

For production CLI use, mount a short-lived administrator JWT and set `PAG_CLI_OIDC_TOKEN_FILE` to its path. `PAG_CLI_OIDC_TOKEN` is supported for constrained automation but is easier to expose through process environments. Supplying both is rejected. Neither value belongs in a `GatewayConfiguration`, image layer, shell argument, log, or exported configuration.

Every provider configuration has a `max_response_bytes` limit, defaulting to 8 MiB and constrained to 1 KiB–64 MiB. The first-party network adapters enforce it as an accumulated limit for the complete decoded response: non-streaming bodies are read incrementally before JSON parsing, while streaming counts the prefetched first chunk and every later chunk. Exceeding it closes the upstream response and terminates with a non-retryable provider failure; it never starts or resumes fallback after bytes have been exposed to the caller. The built-in mock emits only its small deterministic fixture.

Production resolves the default Vertex URL before validation and validates every provider host against `PAG_PROVIDER_HOST_ALLOWLIST` using exact hostname matches. For example, `europe-west1` requires `europe-west1-aiplatform.googleapis.com`. The allow-list is necessary but not sufficient against DNS rebinding or a compromised resolver; also enforce platform egress, DNS, proxy, and network policy.

## Capabilities and routing

Models declare the public union of `chat`, `responses`, `streaming`, `tools`, `structured_output`, and `multimodal` capabilities. Every route target declares the capabilities of its exact provider/upstream-model pair. Configuration validation requires at least one target that can honor the complete advertised model contract; request routing then skips less-capable fallbacks when a request needs a feature they do not support.

Providers and route targets carry the same operator-reviewed `residency` attestation. A route may require one of those labels, and configuration is rejected when the provider/target labels differ or no fully compatible target satisfies the requirement. This is an explicit policy attestation, not an inference that a cloud region automatically meets a legal-residency requirement. Operators must review the provider endpoint/location and enforce matching network policy.

Policy may restrict maximum input characters and deny provider parameters. Quota may constrain requests per minute and concurrent requests. In distributed profiles, Valkey/Redis coordinates both counters across replicas; an in-memory cache is only for single-process development/tests. Concurrent leases use a defensive one-hour TTL and are released on normal completion or stream disconnect. Missing policy or quota means no configured limit for that field; infrastructure-level limits still apply.

## Secret handling

Inject secrets through the deployment platform's secret mechanism and grant the runtime identity access to exact resources only. Use `sslmode=verify-full` with a trusted CA for PostgreSQL TCP whenever the environment supports hostname verification; weaker accepted modes require an explicit threat/risk decision. The Cloud SQL profile uses the platform's local `/cloudsql/` socket/proxy path instead of direct database TCP. Never commit `.env`, provider keys, database credentials, peppers, OIDC secrets, tokens, prompts, or responses. A configuration export contains references and metadata, not secret values.

Changing `PAG_API_KEY_PEPPER` invalidates existing hashes unless a deliberate migration supports multiple pepper generations. Treat rotation as an incident-sensitive operation and follow [the runbook](operations/runbook.md).
