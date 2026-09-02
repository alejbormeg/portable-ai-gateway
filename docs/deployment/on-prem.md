<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# On-premises quickstart

The on-prem profile connects the gateway to customer-operated PostgreSQL, Valkey/Redis-compatible storage, OIDC, and an OpenAI-compatible model endpoint. The gateway does not bundle or download a model server.

Use the mock development profile first. A BYO endpoint introduces model license, data classification, egress, TLS, availability, capability, and cost decisions that the gateway cannot make for the operator.

The shipped on-prem profile contains only the configured BYO provider. If that endpoint is unavailable, the request fails; it never falls back to a synthetic mock response. Add a second real provider only through an explicitly reviewed route whose capabilities and residency satisfy the same contract.

## Required inputs

- gateway image pinned as `repository@sha256:<digest>`;
- corresponding-source HTTPS URL for that exact build;
- PostgreSQL TCP URL with `sslmode=require`, `verify-ca`, or preferably `verify-full`, plus a TLS-enabled Valkey/Redis-compatible URL;
- high-entropy API-key pepper;
- exact OIDC issuer/audience and static RS256 JWKS;
- reviewed `deploy/config/on-prem.yaml` with real model alias/endpoint;
- `PAG_PROVIDER_HOST_ALLOWLIST` containing the exact BYO hostname;
- provider credential in a platform secret, when required.

Do not place secret values in Helm values, Git, container arguments, or shell history.

Kubernetes environment variables sourced from a Secret are read when a container starts. After rotating a database/cache/provider credential, pepper, or JWKS, trigger and verify a controlled Deployment rollout (for example by changing a reviewed `podAnnotations` revision), while preserving overlap required by the identity/cache rotation procedure. The chart cannot safely infer that rollout from an externally managed Secret it is not allowed to read.

## Kubernetes

The chart intentionally does not install PostgreSQL, Valkey, an identity provider, or a model server. Create those dependencies and an externally managed Secret through your approved platform workflow. The default secret keys are `database-url`, `cache-url`, `api-key-pepper`, `oidc-hs256-secret`, and `oidc-jwks-json`; a BYO credential can be exposed from another key through `extraEnv.valueFrom.secretKeyRef`.

Create a private values file containing only non-secret references:

```yaml
# SPDX-License-Identifier: AGPL-3.0-only
image:
  repository: registry.example.invalid/ai/portable-ai-gateway
  digest: sha256:0000000000000000000000000000000000000000000000000000000000000000

gateway:
  appEnvironment: production
  sourceUrl: https://source.example.invalid/portable-ai-gateway/releases/v0.1.0-alpha
  configVersion: REPLACE_WITH_GIT_COMMIT
  gitOpsMode: true
  allowAnonymousDevelopment: false
  providerHostAllowlist: model-server.example.internal
  config:
    api_version: gateway.portable-ai.dev/v1alpha1
    kind: GatewayConfiguration
    metadata:
      version: REPLACE_WITH_GIT_COMMIT
    spec:
      tenants:
        - id: default
          name: On-premises evaluation
          sensitive: false
      providers:
        - id: byo-openai
          tenant_id: default
          kind: openai-compatible
          base_url: https://model-server.example.internal/v1
          credential_env: PAG_PROVIDER_CREDENTIAL_BYO
          residency: on-prem
          native_responses: false
      models:
        - id: portable-chat
          tenant_id: default
          upstream_model: REPLACE_WITH_LOCAL_MODEL
          capabilities:
            chat: true
            responses: false
            streaming: true
      routes:
        - id: on-prem-route
          tenant_id: default
          model_id: portable-chat
          required_residency: on-prem
          targets:
            - provider_id: byo-openai
              upstream_model: REPLACE_WITH_LOCAL_MODEL
              residency: on-prem
              priority: 10
              capabilities:
                chat: true
                responses: false
                streaming: true
                tools: false
                structured_output: false
                multimodal: false
      policies:
        - id: default-policy
          tenant_id: default
          max_input_chars: 32768
      quotas:
        - id: default-quota
          tenant_id: default
          requests_per_minute: 120
          concurrent_requests: 20

existingSecret:
  name: portable-ai-gateway-runtime
  optionalKeys: false
  optionalOidcKeys: true # runtime still requires one of HS256 or JWKS in production

oidc:
  issuer: https://identity.example.invalid/
  audience: portable-ai-gateway

dataPlane:
  extraEnv:
    - name: PAG_PROVIDER_CREDENTIAL_BYO
      valueFrom:
        secretKeyRef:
          name: portable-ai-gateway-provider
          key: api-key

networkPolicy:
  enabled: false # enable only with complete ingress and egress selectors/rules
```

Replace the zero digest, source URL, Git revision, model, endpoint, identities, and Secret names before rendering. An enabled NetworkPolicy with an empty egress list denies all egress. Define complete DNS, database, cache, identity, and provider rules for the selected CNI, then enable and verify default-deny behavior before any non-synthetic use.

Render and review before changing a cluster:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
helm lint deploy/helm/portable-ai-gateway \
  --values deploy/helm/portable-ai-gateway/values-production.yaml \
  --values on-prem-values.yaml
helm template portable-ai-gateway deploy/helm/portable-ai-gateway \
  --namespace portable-ai-gateway \
  --values deploy/helm/portable-ai-gateway/values-production.yaml \
  --values on-prem-values.yaml
```

After explicit cluster-change approval:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
helm upgrade --install portable-ai-gateway deploy/helm/portable-ai-gateway \
  --namespace portable-ai-gateway \
  --create-namespace \
  --values deploy/helm/portable-ai-gateway/values-production.yaml \
  --values on-prem-values.yaml \
  --wait
```

Keep the control-plane Service private. Put TLS/authenticated ingress in front of the data plane and, if remote administration is necessary, a separate restricted path in front of the control plane.

## OpenShift restricted

The OpenShift override requests no fixed UID/GID or custom SCC and keeps non-root, dropped capabilities, seccomp, and read-only root filesystem:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
helm template portable-ai-gateway deploy/helm/portable-ai-gateway \
  --namespace portable-ai-gateway \
  --values deploy/helm/portable-ai-gateway/values-production.yaml \
  --values on-prem-values.yaml \
  --values deploy/helm/portable-ai-gateway/values-openshift.yaml
```

With approval, use the same values in `helm upgrade --install`. The override enables an edge-terminated OpenShift Route for the data plane and leaves the control-plane Route disabled. Confirm the generated host, TLS policy, NetworkPolicy support, restricted-v2 admission, image pull, and writable paths in a non-production namespace.

## Docker Compose with BYO/Vertex egress

The base Compose topology keeps PostgreSQL and Valkey exclusively on an internal backend. Both gateway processes also join a normal bridge so Docker can publish their loopback ports; therefore Compose alone is not an outbound firewall. The provider overlay mounts the reviewed on-prem configuration, injects the provider credential only into the data plane, and adds a provider-specific network only to that plane:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
docker compose -f compose.yaml -f deploy/compose/provider-egress.yaml config
docker compose -f compose.yaml -f deploy/compose/provider-egress.yaml up --build
```

Select/mount the reviewed on-prem configuration and inject `PAG_PROVIDER_CREDENTIAL_BYO` plus the exact provider allow-list through an operator-managed environment/secret mechanism. Reserve every `PAG_PROVIDER_CREDENTIAL_*` variable exclusively for a provider bearer credential. The command can download dependencies/images and creates local resources; inspect and approve it first.

Compose publishes on `127.0.0.1` by default. A deliberate `PAG_BIND_ADDRESS` change is required for remote access; disable anonymous development first and place authenticated TLS ingress/firewall policy in front of both ports. Never expose the control-plane port as a convenience shortcut.

## Acceptance

Verify digest/source link, health/readiness, OIDC and invalid-token behavior, sensitive-tenant API-key denial, cross-tenant negatives, model capability/residency, fail-closed behavior during a BYO provider outage, metadata-only audit, PostgreSQL/cache failure, rollback, backup restore, and a synthetic canary. Test pre-first-byte fallback only after explicitly configuring a second real provider. Generated manifests alone do not support an on-prem portability claim.
