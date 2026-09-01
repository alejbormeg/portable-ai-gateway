# SPDX-License-Identifier: AGPL-3.0-only
variable "project_id" {
  description = "GCP project that will own the gateway resources."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id)) && !startswith(var.project_id, "replace-")
    error_message = "project_id must be an explicit 6-30 character GCP project ID, not a placeholder."
  }
}

variable "region" {
  description = "GCP region for Cloud Run, Cloud SQL and Memorystore."
  type        = string
  default     = "europe-west1"
}

variable "vertex_location" {
  description = "Vertex AI location used by the provider adapter."
  type        = string
  default     = "europe-west1"
}

variable "vertex_residency" {
  description = "Reviewed policy/residency label assigned to the configured Vertex route. It is explicit because geography must not be inferred from a location string."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9._-]{0,63}$", var.vertex_residency))
    error_message = "vertex_residency must be an explicit lowercase policy label."
  }
}

variable "secret_replication_locations" {
  description = "Explicit Secret Manager user-managed replica locations reviewed for the deployment's residency policy."
  type        = set(string)

  validation {
    condition = (
      length(var.secret_replication_locations) > 0
      && alltrue([for location in var.secret_replication_locations : can(regex("^[a-z][a-z0-9-]{1,62}$", location))])
    )
    error_message = "secret_replication_locations must contain at least one explicit GCP location."
  }
}

variable "vertex_model" {
  description = "Exact Vertex AI publisher model or endpoint identifier exposed by the default route."
  type        = string

  validation {
    condition     = length(trimspace(var.vertex_model)) > 0 && !startswith(var.vertex_model, "REPLACE_")
    error_message = "vertex_model must be an explicit non-placeholder model identifier."
  }
}

variable "name" {
  description = "DNS-safe prefix for resource names."
  type        = string
  default     = "portable-ai-gateway"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,38}[a-z0-9]$", var.name))
    error_message = "name must be 3-40 lowercase alphanumeric or hyphen characters and start with a letter."
  }
}

variable "image" {
  description = "Public or otherwise Cloud Run-accessible OCI image pinned by sha256 digest."
  type        = string

  validation {
    condition     = can(regex("^[^[:space:]@]+@sha256:[0-9a-f]{64}$", var.image))
    error_message = "image must be immutable and use repository@sha256:<64 lowercase hex characters>."
  }
}

variable "source_url" {
  description = "Public corresponding-source URL advertised by AGPL network responses."
  type        = string

  validation {
    condition     = can(regex("^https://", var.source_url))
    error_message = "source_url must be an HTTPS URL."
  }
}

variable "database_name" {
  description = "PostgreSQL database name."
  type        = string
  default     = "portable_gateway"
}

variable "database_user" {
  description = "PostgreSQL application user."
  type        = string
  default     = "portable_gateway"
}

variable "database_password" {
  description = "PostgreSQL password. Sensitive values are stored in Terraform state; use an encrypted remote backend and restrict state access."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.database_password) >= 20
    error_message = "database_password must be at least 20 characters."
  }
}

variable "api_key_pepper" {
  description = "High-entropy server-side pepper for hashing gateway API keys. Stored in Terraform state."
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.api_key_pepper) >= 32
    error_message = "api_key_pepper must be at least 32 characters."
  }
}

variable "oidc_issuer" {
  description = "OIDC issuer URL required by the production GCP profile."
  type        = string

  validation {
    condition     = can(regex("^https://[^[:space:]]+$", var.oidc_issuer))
    error_message = "oidc_issuer must be a non-empty HTTPS URL."
  }
}

variable "oidc_audience" {
  description = "OIDC audience required by the gateway."
  type        = string

  validation {
    condition     = length(trimspace(var.oidc_audience)) > 0
    error_message = "oidc_audience must not be empty."
  }
}

variable "oidc_jwks_json" {
  description = "Static JWKS JSON required by the production GCP profile. Stored in Terraform state."
  type        = string
  sensitive   = true

  validation {
    condition     = can(jsondecode(var.oidc_jwks_json)) && try(length(jsondecode(var.oidc_jwks_json).keys) > 0, false)
    error_message = "oidc_jwks_json must be a valid JWKS document containing at least one key."
  }
}

variable "gateway_config_yaml" {
  description = "Optional complete gateway v1alpha1 YAML. Null creates a Vertex example derived from project_id and vertex_location."
  type        = string
  default     = null
}

variable "network_cidr" {
  description = "Primary subnet CIDR for serverless private connectivity."
  type        = string
  default     = "10.80.0.0/24"
}

variable "vpc_connector_cidr" {
  description = "Dedicated /28 CIDR for the Serverless VPC Access connector."
  type        = string
  default     = "10.80.1.0/28"
}

variable "cloud_sql_tier" {
  description = "Cloud SQL machine tier."
  type        = string
  default     = "db-custom-1-3840"
}

variable "cloud_sql_availability_type" {
  description = "ZONAL for evaluation or REGIONAL for production."
  type        = string
  default     = "ZONAL"

  validation {
    condition     = contains(["ZONAL", "REGIONAL"], var.cloud_sql_availability_type)
    error_message = "cloud_sql_availability_type must be ZONAL or REGIONAL."
  }
}

variable "cloud_sql_deletion_protection" {
  description = "Protect the Cloud SQL instance from accidental Terraform destruction."
  type        = bool
  default     = true
}

variable "cache_memory_size_gb" {
  description = "Memorystore capacity in GiB."
  type        = number
  default     = 1
}

variable "cache_tier" {
  description = "BASIC for evaluation or STANDARD_HA for high availability."
  type        = string
  default     = "BASIC"

  validation {
    condition     = contains(["BASIC", "STANDARD_HA"], var.cache_tier)
    error_message = "cache_tier must be BASIC or STANDARD_HA."
  }
}

variable "data_plane_min_instances" {
  type        = number
  description = "Minimum data-plane Cloud Run instances."
  default     = 0
}

variable "data_plane_max_instances" {
  type        = number
  description = "Maximum data-plane Cloud Run instances."
  default     = 10
}

variable "control_plane_min_instances" {
  type        = number
  description = "Minimum control-plane Cloud Run instances."
  default     = 0
}

variable "control_plane_max_instances" {
  type        = number
  description = "Maximum control-plane Cloud Run instances."
  default     = 3
}

variable "data_plane_invoker_members" {
  description = "IAM members allowed to invoke the data plane. Use allUsers only when application-layer authentication is intentionally exposed."
  type        = set(string)
  default     = []
}

variable "control_plane_invoker_members" {
  description = "IAM members allowed to invoke the control plane. Empty keeps it private."
  type        = set(string)
  default     = []
}

variable "control_plane_ingress" {
  description = "Cloud Run ingress policy for the administrative service. Keep INTERNAL_ONLY unless an approved load-balancer design requires INTERNAL_LOAD_BALANCER."
  type        = string
  default     = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  validation {
    condition = contains([
      "INGRESS_TRAFFIC_INTERNAL_ONLY",
      "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
    ], var.control_plane_ingress)
    error_message = "The control plane may use only internal Cloud Run ingress modes."
  }
}

variable "labels" {
  description = "Additional labels applied to supported resources."
  type        = map(string)
  default     = {}
}
