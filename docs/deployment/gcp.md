<!-- SPDX-License-Identifier: AGPL-3.0-only -->

# GCP quickstart

The GCP profile provisions new Cloud Run data/control services, Cloud SQL for PostgreSQL, Memorystore Redis-compatible cache, private networking, Secret Manager entries, and dedicated service accounts. The data plane can call Vertex AI through Application Default Credentials. This path is billable and changes IAM, APIs, networking, data services, secrets, and Cloud Run.

Nothing in this repository authorizes an apply. Use a dedicated evaluation project, preserve all existing resources, estimate a maximum budget, and obtain explicit approval for credentials, plan/apply, inference, and cost.

## Preconditions

- Terraform version/provider constraints from `infra/terraform/gcp/versions.tf`;
- a GCP project/region that satisfy organization policy and service availability;
- sufficient permissions to inspect and create every resource in the plan;
- a gateway image already accessible to Cloud Run and pinned by SHA-256 digest;
- corresponding source for that digest;
- approved Vertex model/location, explicit reviewed `vertex_residency` policy label, and synthetic test data;
- an exact provider-host allow-list containing `<vertex_location>-aiplatform.googleapis.com` (Terraform injects it into both gateway processes);
- production OIDC issuer, audience, and static JWKS;
- explicit Secret Manager user-managed replica locations aligned with the approved residency policy;
- encrypted/restricted Terraform state, because database password, API-key pepper, JWKS, and generated service credentials are sensitive.

The standalone template `deploy/config/gcp.yaml` contains `REPLACE_WITH_VERTEX_MODEL`. Terraform does not use a fake default: `vertex_model` is required and validation rejects an empty or `REPLACE_*` value. Set an approved model before plan. Do not upload service-account JSON keys.

## Plan without applying

```sh
# SPDX-License-Identifier: AGPL-3.0-only
cd infra/terraform/gcp
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform fmt -check
terraform validate
terraform plan -out=portable-ai-gateway.tfplan
terraform show portable-ai-gateway.tfplan
```

`terraform init` downloads providers and therefore needs network/download approval. `terraform.tfvars` and the saved plan can contain sensitive values; keep them out of Git and protect/delete them according to policy.

Review the complete resource and IAM graph, enabled APIs, deletion protection, ingress/IAM invokers, region/residency, network ranges, managed-service tier, backups, scaling bounds, secret versions/replicas, labels, and estimated cost. `vertex_residency` is an operator-reviewed routing label; Terraform does not infer or prove legal residency from a Google location name. Secret Manager uses only the explicit user-managed `secret_replication_locations`. Empty invoker sets add no Cloud Run invoker grants; they do not remove broader project/folder/organization IAM grants, which must also be audited.

## Apply only after approval

```sh
# SPDX-License-Identifier: AGPL-3.0-only
terraform apply portable-ai-gateway.tfplan
terraform output
```

Do not reuse a stale plan after variables, credentials, source, image digest, organization policy, or cost approval changes.

## Verify

Use authenticated Cloud Run invocation plus application OIDC as designed; these are distinct layers. When both layers are enabled, place the Google-signed Cloud Run ID token in `X-Serverless-Authorization` and the gateway OIDC token in `Authorization`, then verify both independently. Confirm both service accounts have only required roles, inherited IAM has been reviewed, the resolved Vertex hostname exactly matches `PAG_PROVIDER_HOST_ALLOWLIST`, Cloud SQL uses the mounted local `/cloudsql/` Unix-socket path with private connectivity/backups, cache transport/auth are enabled, Secret Manager access/replication is scoped, the source link matches the deployed digest, and the control plane remains on internal ingress. The socket path is the intentional exception to the runtime's production PostgreSQL TCP `sslmode` requirement.

Run only approved synthetic canaries. Validate Chat Completions, invalid auth, tenant isolation, Vertex model/residency, audit redaction, provider timeout/quota, database/cache failure, scaling, and rollback. Record actual Cloud Billing data or a clearly labeled estimate.

## Cleanup

Before any destroy, inventory exact resources, preserve approved evidence, inspect a fresh destroy plan, handle Cloud SQL deletion protection deliberately, and obtain explicit destructive-action approval. Never target an entire shared project or pre-existing resource. Record read-only absence verification and residual costs in the PoC cleanup/cost reports.

Terraform generation and validation do not demonstrate that this GCP profile deployed successfully; only executed evidence against one immutable digest can support that claim.
