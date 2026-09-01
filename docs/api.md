<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# HTTP API contract

Status: `v0.1.0-alpha`. The URL families are intentionally versioned, but backward compatibility is not guaranteed before a published stabilization policy.

The `/v1/*` surface is a provider-dependent **de facto compatibility profile**, not an open standard, certification, or claim of complete behavioral equivalence. The reference shapes are OpenAI's [Chat Completions create API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) and [Responses streaming events](https://developers.openai.com/api/reference/resources/responses/streaming-events). Portable AI Gateway is not affiliated with or certified by OpenAI. Only the scenarios in the executed conformance matrix support compatibility claims for a specific gateway/provider/model/version combination.

## Common behavior

All endpoints return `X-Request-ID`. A caller-supplied ID is preserved only when it is 1–128 characters from letters, digits, `.`, `_`, `:`, and `-`; otherwise the gateway generates a UUID. When `PAG_SOURCE_URL` is configured, every response includes `Link: <URL>; rel="source"`.

`GET /`, `GET /health`, and `GET /ready` are unauthenticated system endpoints on both process modes. Health means process liveness. Control-plane readiness checks durable store and cache connectivity; data-plane readiness additionally requires at least one active provider and route. A failed condition returns `503 not_ready`.

Errors use an OpenAI-style content-free envelope:

```jsonc
// SPDX-License-Identifier: AGPL-3.0-only
{
  "error": {
    "message": "Stable human-readable summary",
    "type": "invalid_request_error",
    "code": "stable_machine_code"
  }
}
```

Do not parse the message for automation; use HTTP status and `error.code`. Validation details and upstream bodies are deliberately not reflected. Inference authentication is resolved before the request body is consumed. JSON bodies are then read incrementally and requests larger than `PAG_MAX_REQUEST_BYTES`, whether declared by `Content-Length` or observed while streaming, return `413 payload_too_large`.

## Data plane

| Method and path | Required scope | Contract |
| --- | --- | --- |
| `GET /` | Public | Name, version, and corresponding-source URL. |
| `GET /health` | Public | Liveness and source URL. |
| `GET /ready` | Public | Store/cache readiness plus active provider/route on the data plane. |
| `GET /v1/models` | `models:read` | OpenAI-style list of active model aliases routed for the authenticated tenant. |
| `POST /v1/chat/completions` | `inference:write` | Portable OpenAI-compatible Chat Completions path. |
| `POST /v1/responses` | `inference:write` | Native provider passthrough only; no Chat Completions emulation. |

The bearer credential determines tenant. Sensitive tenants reject API-key authentication. A requested model is a customer-facing alias; the route substitutes provider-specific upstream model names.

Chat requires a non-empty `messages` array. Supported roles are `system`, `user`, `assistant`, and `tool`; a tool message requires `tool_call_id`. Responses requires a non-null `input`. `model` is a gateway alias of at most 128 characters using letters, digits, `.`, `_`, and `-`; arbitrary provider model paths belong in protected route configuration and cannot be smuggled into audit metadata. `stream` must be a JSON boolean; the alpha does not coerce scalar types. Extra OpenAI-compatible fields can pass through unchanged, but model/target capability and tenant policy reject tools, structured output, multimodal input, streaming, or denied parameters when no eligible target can honor them.

### Capability admission in the alpha

The gateway derives one immutable set of requested capabilities and applies it first to the public model declaration and then to every route target. A missing model capability returns `400 unsupported_capability`; a target missing the same capability is skipped, allowing ordered fallback, and exhaustion returns `503 no_eligible_provider`.

The `v0.1.0-alpha` detector is deliberately bounded to these request indicators:

| Capability | Indicators treated as requiring it |
| --- | --- |
| Tools | Presence of top-level `tools`, `tool_choice`, `parallel_tool_calls`, legacy `functions`, or legacy `function_call`; a `tool` role; `tool_calls`, `tool_call_id`, or `function_call` in `messages`/`input`; and interaction types `function_call`, `function_call_output`, `tool_call`, or `tool_result`. |
| Structured output | Presence of top-level `response_format`, or top-level Responses `text.format`. Function parameter JSON Schemas are opaque tool definitions and do not imply structured output. |
| Multimodal | Any non-`text` value in top-level `modalities`; direct media fields; or image, audio, video, and file content types/fields recursively under `messages` or `input`. This includes the common `*_url`, `*_data`, `file_id`, and `input_*`/`output_*` forms enumerated by the implementation. |

Field presence is fail-closed even when a collection is empty or a choice says `none`, because the alpha does not normalize or prove provider-specific semantics. This admission check only prevents routing a declared request feature to an undeclared model/target. It does not certify every provider dialect, validate tool schemas, translate multimodal formats, or promise that future provider fields are automatically recognized; new indicators require an explicit contract and conformance tests.

Example using the anonymous synthetic development profile:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
curl --fail-with-body \
  --header 'Content-Type: application/json' \
  --header 'X-Request-ID: api-example-001' \
  --data '{"model":"mock-chat","messages":[{"role":"user","content":"synthetic"}]}' \
  http://127.0.0.1:8080/v1/chat/completions
```

For authenticated environments add `Authorization: Bearer <OIDC-JWT-or-pag-key>`. Do not place credentials in committed scripts or examples.

Successful inference responses add `X-Provider-ID` and `X-Route-ID`. Only the safe upstream response header `Retry-After` is eligible for forwarding. Every `/v1/*` and `/admin/v1/*` response is forced to `Cache-Control: no-store`; provider-controlled cache policy is never propagated.

### Streaming and fallback

`stream: true` returns `text/event-stream`, `Cache-Control: no-store`, and `X-Accel-Buffering: no`. The provider adapter must obtain the first upstream chunk before the gateway commits the response. Retryable failure may advance to the next eligible target only before that point. After the client receives bytes, an upstream error terminates the stream; the gateway does not replay it. The terminal stream result—success, interruption, or client cancellation—is then written to metadata-only audit.

The generic adapter passes provider SSE bytes through after validating the content type. Chat and Responses event shapes therefore remain the selected provider's native compatible contract. Test each provider/model capability rather than assuming that identical paths imply identical semantics.

## Control plane

Every administrative endpoint requires an OIDC JWT and administrator scope. The audit-list endpoint additionally permits a dedicated OIDC `audit:read` identity; that scope grants no configuration or key operation. Keep this process on a separate private origin from the data plane.

Generic resources are `tenants`, `providers`, `models`, `routes`, `policies`, and `quotas`:

| Method and path | Behavior |
| --- | --- |
| `GET /admin/v1/{resource}?tenant_id=...` | List, optionally tenant-filtered. |
| `POST /admin/v1/{resource}` | Create; `201`; duplicate ID is `409 resource_exists`. |
| `GET /admin/v1/{resource}/{id}` | Fetch one. |
| `PUT /admin/v1/{resource}/{id}` | Full replacement; body ID must match path. |
| `DELETE /admin/v1/{resource}/{id}` | Delete; `204`. Providers/models referenced by active routes return `409 resource_in_use`. Tenant deletion atomically revokes its keys and removes its owned configuration; audit history is retained. |

Unknown fields are rejected by the resource schemas. Cross-resource validation checks tenant ownership, provider URL policy, routes, native Responses capability, and unique active relationships. In GitOps mode, generic create/replace/delete operations return `409 gitops_read_only`; use the CLI apply workflow.

API-key lifecycle:

| Method and path | Behavior |
| --- | --- |
| `GET /admin/v1/keys?tenant_id=...` | List key metadata; never returns hashes or secret values. |
| `POST /admin/v1/keys` | Issue for a non-sensitive tenant. Requires tenant, name, one or both of `models:read` and `inference:write`, and future `expires_at`; plaintext `key` appears once. Wildcard/admin scopes are rejected. |
| `GET /admin/v1/keys/{id}` | Fetch metadata. |
| `POST /admin/v1/keys/{id}/rotate` | Replace secret and return the new plaintext once. |
| `POST /admin/v1/keys/{id}/revoke` | Revoke immediately. |
| `GET /admin/v1/audit?tenant_id=...&limit=...` | Return newest metadata-only events; limit is clamped to 1–1000. |

Keys and audit endpoints remain administrative operations in GitOps mode because keys are secrets/lifecycle records rather than desired-state YAML. Restrict their automation identities separately.

## CLI contract

Use the module entry point so source and container invocations are explicit:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
export PAG_APP_ENV=development
export PAG_DATABASE_URL=sqlite:///./portable-ai-gateway.db
export PAG_CACHE_URL=memory://
export PAG_CONFIG_PATH=deploy/config/development.yaml
python -m portable_ai_gateway cli bootstrap --tenant-id default --api-key-expiry-days 30
python -m portable_ai_gateway cli config validate --file deploy/config/development.yaml
python -m portable_ai_gateway cli config apply --file deploy/config/development.yaml
python -m portable_ai_gateway cli config export --output - --version REPLACE_WITH_REVISION
python -m portable_ai_gateway cli doctor
python -m portable_ai_gateway cli provider check --provider-id mock
# Use --upstream-model only when the provider has zero/multiple active routed models.
```

`bootstrap --sensitive` never issues an API key; `--no-api-key` disables issue for any tenant, and bootstrap is rejected in GitOps mode. Every stateful CLI command produces metadata-only audit records: development/test uses the explicit actor `cli:local`, while production requires and records the verified OIDC administrator subject. CLI output is JSON/YAML intended for automation, but a bootstrap/rotation secret still requires protected capture. Provider check resolves the unique active target's configured upstream model, or requires explicit `--upstream-model` when resolution is ambiguous. It can make a real external request for non-mock adapters and therefore needs credentials, synthetic payload approval, network approval, and possible cost approval.

## OpenAPI discovery

FastAPI-generated `/openapi.json`, `/docs`, and `/redoc` are convenience views in the alpha, not a substitute for conformance tests. Restrict the control-plane origin even if schema discovery itself appears harmless.
