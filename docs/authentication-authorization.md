<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Authentication and authorization

Portable AI Gateway accepts bearer authentication at the HTTP boundary. The token is either an OIDC-compatible JWT or a generated gateway API key beginning with `pag.`. Authentication determines the actor, tenant, scopes, and method; clients cannot override the resulting tenant through a request body or header.

## OIDC/JWT

The alpha verifier is deliberately offline: verification material is injected through configuration, and the gateway does not call an identity provider's discovery or JWKS endpoint at request time.

Supported signature algorithms are `RS256` with a statically supplied JWKS and `HS256` with a statically supplied secret. Prefer `RS256` for production identity providers; limit `HS256` to environments where shared-secret distribution is explicitly accepted. Algorithm selection comes from the signed token header but succeeds only when matching verification material is configured; `none` and other algorithms are rejected.

Required claims:

- `sub`: printable actor identifier of at most 256 characters, without control characters;
- `iss`: exact match to `PAG_OIDC_ISSUER`;
- `aud`: string or array containing `PAG_OIDC_AUDIENCE`;
- `exp`: numeric expiry, with a 30-second verification allowance;
- `nbf`: when present, numeric and active within the same 30-second allowance;
- `scope` as a space-delimited string or `scp` as string/list;
- `tenant_id` or `tid` for tenant-scoped inference, using the gateway identifier grammar.

Administration requires a JWT plus `admin`, `admin:*`, or `*`. Reading `/admin/v1/audit` may instead use the dedicated JWT scope `audit:read`. API keys are never administrative credentials. Inference requires `inference:write` and a valid tenant claim.

Production CLI commands that open the durable store are administrative operations too. They require the same JWT administrator identity through `PAG_CLI_OIDC_TOKEN_FILE` (preferred secret-file mount) or `PAG_CLI_OIDC_TOKEN`; the token is never accepted as a command-line argument. `cli config validate` is the only exception because it validates a local document without opening or changing runtime state. Development/test mode may use the explicit `cli:local` actor. Every stateful CLI command records the resolved actor in the metadata-only audit log.

Static JWKS means key rotation is a deployment/configuration operation. Load the new key set, roll out safely, verify both active keys during overlap, then remove the retired key. Do not fetch keys dynamically through an unreviewed network path.

## Gateway API keys

An issued key has the shape `pag.<key-id>.<random-secret>`. The plaintext key is returned only at issue or rotation time. The durable store retains an HMAC-SHA-256 hash using the server-side `PAG_API_KEY_PEPPER`, along with key ID, tenant, name, scopes, expiration, revocation time, creation time, and last-use time.

Every issued key must have a future expiry and the minimum scopes required. The only API-key scopes are `models:read` and `inference:write`; wildcard, audit and administrator scopes are rejected. Rotation replaces the secret for the same key ID; revocation marks it unusable. Changing the global pepper without a migration invalidates every stored key hash.

API keys are intended only for approved non-sensitive tenants. If a tenant is marked `sensitive`, inference authenticated with an API key is rejected even if its scopes otherwise match; use OIDC.

## Development mode

`PAG_ALLOW_ANONYMOUS_DEV=true` creates an anonymous synthetic context for local evaluation. Startup rejects this setting when `PAG_APP_ENV=production`. Never expose the development profile to an untrusted network or use it to assess production authorization.

## Failure behavior

Missing, malformed, invalid, expired, not-yet-active, revoked, or unverifiable credentials return a content-free OpenAI-style error and `WWW-Authenticate: Bearer` where applicable. Valid identity with insufficient scope returns `403`. Authentication, tenant resolution, policy, and residency failures fail closed and are recorded as metadata-only audit decisions.

## Deployment checklist

- Terminate TLS and prevent untrusted direct access around the authenticated edge.
- Use exact issuer/audience values and pin approved JWKS keys.
- Map groups/roles to narrow scopes at the identity provider; do not grant wildcard scope to workloads.
- Separate administrator, GitOps automation, inference, and audit-reader identities.
- Store peppers and signing material in platform secret stores, not YAML, image layers, logs, or shell history.
- Test missing/invalid tokens, wrong tenant, wrong audience/issuer, expiry, revocation, and sensitive-tenant API-key denial before routing real traffic.
- Monitor metadata-only authentication failure rates without logging token bytes.
