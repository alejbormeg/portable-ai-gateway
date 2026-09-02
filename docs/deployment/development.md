<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# Development quickstart

This profile proves a local, deterministic request path without cloud credentials or a model server. It uses the mock provider, PostgreSQL, Valkey, anonymous development authentication, and synthetic configuration. It is not a production security profile.

## Prerequisites

- Docker Engine with Compose v2;
- enough local capacity for four containers;
- permission to download the Python base image, dependencies, PostgreSQL, and Valkey images.

`docker compose up --build` can download packages and images. In controlled or metered environments, obtain approval and inspect the resolved build/Compose configuration first.

## Start

```sh
# SPDX-License-Identifier: AGPL-3.0-only
docker compose config --quiet
docker compose up --build
```

The data plane listens on `http://127.0.0.1:8080` and the control plane on `http://127.0.0.1:8081`. Compose binds both explicitly to loopback by default. The gateways join a host-access bridge because current Docker Engine versions do not publish ports for containers attached exclusively to an internal network; PostgreSQL and Valkey remain exclusively on the internal backend. This bridge is not an outbound firewall, although the mock profile makes no external provider call. Anonymous development mode grants a synthetic wildcard identity only on data-plane authentication; the control plane still requires its configured administrator OIDC token. `PAG_BIND_ADDRESS` exists for deliberate on-prem exposure, but it must never be changed to a non-loopback address while anonymous mode is enabled.

From another terminal:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
curl --fail-with-body http://127.0.0.1:8080/health
curl --fail-with-body http://127.0.0.1:8080/ready
curl --fail-with-body http://127.0.0.1:8080/v1/models

curl --fail-with-body \
  --header 'Content-Type: application/json' \
  --header 'X-Request-ID: dev-quickstart-001' \
  --data '{"model":"mock-chat","messages":[{"role":"user","content":"synthetic test"}]}' \
  http://127.0.0.1:8080/v1/chat/completions
```

For the same endpoint and canonical request payload, the mock returns the same complete JSON response byte-for-byte: content is `mock response`, `created` is fixed to `0`, and the identifier is a SHA-256-derived value. Change the payload when a distinct deterministic fixture is required.

Native Responses is enabled for the mock profile:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
curl --fail-with-body \
  --header 'Content-Type: application/json' \
  --data '{"model":"mock-chat","input":"synthetic test"}' \
  http://127.0.0.1:8080/v1/responses
```

## CLI without containers

If development dependencies are already installed in an approved environment, the process entry points are:

```sh
# SPDX-License-Identifier: AGPL-3.0-only
export PAG_APP_ENV=development
export PAG_DATABASE_URL=sqlite:///./portable-ai-gateway.db
export PAG_CACHE_URL=memory://
export PAG_CONFIG_PATH=deploy/config/development.yaml
export PAG_ALLOW_ANONYMOUS_DEV=true
python -m portable_ai_gateway cli config validate --file deploy/config/development.yaml
python -m portable_ai_gateway cli doctor
python -m portable_ai_gateway data-plane --host 127.0.0.1 --port 8080
python -m portable_ai_gateway control-plane --host 127.0.0.1 --port 8081
```

The two HTTP processes are separate long-running commands; start them in separate terminals with the same variables. The SQLite/`memory://` form is a single-process evaluation profile, not a shared multi-process topology. Do not install/download dependencies merely because this command is documented; follow your environment's approval process.

## Stop and clean up

```sh
# SPDX-License-Identifier: AGPL-3.0-only
docker compose down
```

This retains synthetic PostgreSQL and Valkey volumes. After resolving exact project-owned volumes and deciding that their evidence is no longer required, `docker compose down --volumes` removes them. Never use a global prune command for this quickstart.

If readiness or inference fails, record the image/config version and metadata-only logs, then follow [the operator runbook](../operations/runbook.md). A successful local request does not validate GCP, OpenShift, air-gap, OIDC, or performance claims.
