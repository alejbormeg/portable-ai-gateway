# SPDX-License-Identifier: AGPL-3.0-only
locals {
  labels = merge({
    application  = "portable-ai-gateway"
    "managed-by" = "terraform"
  }, var.labels)

  required_services = toset([
    "aiplatform.googleapis.com",
    "compute.googleapis.com",
    "iam.googleapis.com",
    "redis.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
    "vpcaccess.googleapis.com",
  ])

  gateway_config_yaml = coalesce(var.gateway_config_yaml, yamlencode({
    api_version = "gateway.portable-ai.dev/v1alpha1"
    kind        = "GatewayConfiguration"
    metadata = {
      version = "terraform-gcp-v1"
    }
    spec = {
      tenants = [{
        id        = "default"
        name      = "GCP evaluation"
        sensitive = false
      }]
      providers = [{
        id               = "vertex"
        tenant_id        = "default"
        kind             = "vertex"
        project          = var.project_id
        location         = var.vertex_location
        residency        = var.vertex_residency
        native_responses = false
      }]
      models = [{
        id             = "vertex-chat"
        tenant_id      = "default"
        upstream_model = var.vertex_model
        capabilities = {
          chat      = true
          responses = false
          streaming = true
        }
      }]
      routes = [{
        id                 = "vertex-route"
        tenant_id          = "default"
        model_id           = "vertex-chat"
        required_residency = var.vertex_residency
        targets = [{
          provider_id    = "vertex"
          upstream_model = var.vertex_model
          residency      = var.vertex_residency
          priority       = 10
          capabilities = {
            chat              = true
            responses         = false
            streaming         = true
            tools             = false
            structured_output = false
            multimodal        = false
          }
        }]
      }]
      policies = [{
        id              = "default-policy"
        tenant_id       = "default"
        max_input_chars = 32768
      }]
      quotas = [{
        id                  = "default-quota"
        tenant_id           = "default"
        requests_per_minute = 120
        concurrent_requests = 20
      }]
    }
  }))

  database_url = "postgresql://${urlencode(var.database_user)}:${urlencode(var.database_password)}@/${urlencode(var.database_name)}?host=${urlencode("/cloudsql/${google_sql_database_instance.gateway.connection_name}")}"
  # Memorystore AUTH is a single password (not an ACL username/password pair).
  cache_url = "rediss://:${urlencode(google_redis_instance.gateway.auth_string)}@${google_redis_instance.gateway.host}:${google_redis_instance.gateway.port}/0?ssl_ca_certs=${urlencode("/var/run/secrets/cache/ca.pem")}&ssl_cert_reqs=required"
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "gateway" {
  name                    = "${var.name}-network"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"

  depends_on = [google_project_service.required]
}

resource "google_compute_subnetwork" "gateway" {
  name                     = "${var.name}-${var.region}"
  region                   = var.region
  network                  = google_compute_network.gateway.id
  ip_cidr_range            = var.network_cidr
  private_ip_google_access = true
}

resource "google_compute_global_address" "private_services" {
  name          = "${var.name}-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.gateway.id

  depends_on = [google_project_service.required]
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.gateway.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}

resource "google_vpc_access_connector" "gateway" {
  name          = substr("${var.name}-connector", 0, 24)
  region        = var.region
  network       = google_compute_network.gateway.name
  ip_cidr_range = var.vpc_connector_cidr
  min_instances = 2
  max_instances = 3

  depends_on = [google_project_service.required]
}

resource "google_service_account" "data_plane" {
  account_id   = substr("${var.name}-data", 0, 30)
  display_name = "Portable AI Gateway data plane"

  depends_on = [google_project_service.required]
}

resource "google_service_account" "control_plane" {
  account_id   = substr("${var.name}-control", 0, 30)
  display_name = "Portable AI Gateway control plane"

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "data_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.data_plane.email}"
}

resource "google_project_iam_member" "data_cloud_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.data_plane.email}"
}

resource "google_project_iam_member" "control_cloud_sql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.control_plane.email}"
}

resource "google_sql_database_instance" "gateway" {
  name                = "${var.name}-postgres"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = var.cloud_sql_deletion_protection

  settings {
    tier              = var.cloud_sql_tier
    availability_type = var.cloud_sql_availability_type
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.gateway.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    user_labels = local.labels
  }

  depends_on = [
    google_project_service.required,
    google_service_networking_connection.private_services,
  ]
}

resource "google_sql_database" "gateway" {
  name     = var.database_name
  instance = google_sql_database_instance.gateway.name
}

resource "google_sql_user" "gateway" {
  name     = var.database_user
  instance = google_sql_database_instance.gateway.name
  password = var.database_password
}

resource "google_redis_instance" "gateway" {
  name                    = "${var.name}-cache"
  region                  = var.region
  tier                    = var.cache_tier
  memory_size_gb          = var.cache_memory_size_gb
  redis_version           = "REDIS_7_2"
  authorized_network      = google_compute_network.gateway.id
  connect_mode            = "PRIVATE_SERVICE_ACCESS"
  reserved_ip_range       = google_compute_global_address.private_services.name
  auth_enabled            = true
  transit_encryption_mode = "SERVER_AUTHENTICATION"
  labels                  = local.labels

  depends_on = [
    google_project_service.required,
    google_service_networking_connection.private_services,
  ]
}

resource "google_secret_manager_secret" "database_url" {
  secret_id = "${var.name}-database-url"
  labels    = local.labels
  replication {
    user_managed {
      dynamic "replicas" {
        for_each = var.secret_replication_locations
        content {
          location = replicas.value
        }
      }
    }
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "database_url" {
  secret      = google_secret_manager_secret.database_url.id
  secret_data = local.database_url
}

resource "google_secret_manager_secret" "cache_url" {
  secret_id = "${var.name}-cache-url"
  labels    = local.labels
  replication {
    user_managed {
      dynamic "replicas" {
        for_each = var.secret_replication_locations
        content {
          location = replicas.value
        }
      }
    }
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "cache_url" {
  secret      = google_secret_manager_secret.cache_url.id
  secret_data = local.cache_url
}

resource "google_secret_manager_secret" "cache_ca" {
  secret_id = "${var.name}-cache-ca"
  labels    = local.labels
  replication {
    user_managed {
      dynamic "replicas" {
        for_each = var.secret_replication_locations
        content {
          location = replicas.value
        }
      }
    }
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "cache_ca" {
  secret = google_secret_manager_secret.cache_ca.id
  # Trust every CA published during the documented rotation overlap window.
  secret_data = join("\n", [for certificate in google_redis_instance.gateway.server_ca_certs : certificate.cert])
}

resource "google_secret_manager_secret" "api_key_pepper" {
  secret_id = "${var.name}-api-key-pepper"
  labels    = local.labels
  replication {
    user_managed {
      dynamic "replicas" {
        for_each = var.secret_replication_locations
        content {
          location = replicas.value
        }
      }
    }
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "api_key_pepper" {
  secret      = google_secret_manager_secret.api_key_pepper.id
  secret_data = var.api_key_pepper
}

resource "google_secret_manager_secret" "gateway_config" {
  secret_id = "${var.name}-config"
  labels    = local.labels
  replication {
    user_managed {
      dynamic "replicas" {
        for_each = var.secret_replication_locations
        content {
          location = replicas.value
        }
      }
    }
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "gateway_config" {
  secret      = google_secret_manager_secret.gateway_config.id
  secret_data = local.gateway_config_yaml
}

resource "google_secret_manager_secret" "oidc_jwks" {
  secret_id = "${var.name}-oidc-jwks"
  labels    = local.labels
  replication {
    user_managed {
      dynamic "replicas" {
        for_each = var.secret_replication_locations
        content {
          location = replicas.value
        }
      }
    }
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "oidc_jwks" {
  secret      = google_secret_manager_secret.oidc_jwks.id
  secret_data = var.oidc_jwks_json
}

locals {
  shared_secret_ids = toset([
    google_secret_manager_secret.database_url.secret_id,
    google_secret_manager_secret.cache_url.secret_id,
    google_secret_manager_secret.cache_ca.secret_id,
    google_secret_manager_secret.api_key_pepper.secret_id,
    google_secret_manager_secret.gateway_config.secret_id,
  ])

  secret_access = {
    for item in setproduct(
      local.shared_secret_ids,
      toset([
        google_service_account.data_plane.email,
        google_service_account.control_plane.email,
      ])
      ) : "${item[0]}|${item[1]}" => {
      secret_id = item[0]
      email     = item[1]
    }
  }
}

resource "google_secret_manager_secret_iam_member" "shared" {
  for_each = local.secret_access

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value.email}"
}

resource "google_secret_manager_secret_iam_member" "oidc_jwks" {
  for_each = {
    data_plane    = google_service_account.data_plane.email
    control_plane = google_service_account.control_plane.email
  }

  project   = var.project_id
  secret_id = google_secret_manager_secret.oidc_jwks.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

resource "google_cloud_run_v2_service" "data_plane" {
  name                = "${var.name}-data-plane"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  labels              = local.labels

  template {
    service_account                  = google_service_account.data_plane.email
    timeout                          = "300s"
    max_instance_request_concurrency = 80

    scaling {
      min_instance_count = var.data_plane_min_instances
      max_instance_count = var.data_plane_max_instances
    }

    vpc_access {
      connector = google_vpc_access_connector.gateway.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.gateway.connection_name]
      }
    }

    volumes {
      name = "gateway-config"
      secret {
        secret = google_secret_manager_secret.gateway_config.secret_id
        items {
          version = google_secret_manager_secret_version.gateway_config.version
          path    = "config.yaml"
          mode    = 292
        }
      }
    }

    volumes {
      name = "cache-ca"
      secret {
        secret = google_secret_manager_secret.cache_ca.secret_id
        items {
          version = google_secret_manager_secret_version.cache_ca.version
          path    = "ca.pem"
          mode    = 292
        }
      }
    }

    containers {
      image = var.image
      args  = ["data-plane", "--host", "0.0.0.0", "--port", "8080"]

      ports {
        name           = "http1"
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      volume_mounts {
        name       = "gateway-config"
        mount_path = "/etc/portable-ai-gateway"
      }
      volume_mounts {
        name       = "cache-ca"
        mount_path = "/var/run/secrets/cache"
      }

      env {
        name  = "PAG_APP_ENV"
        value = "production"
      }
      env {
        name  = "PAG_CONFIG_PATH"
        value = "/etc/portable-ai-gateway/config.yaml"
      }
      env {
        name  = "PAG_CONFIG_VERSION"
        value = "terraform-gcp-v1alpha1"
      }
      env {
        name  = "PAG_GITOPS_MODE"
        value = "true"
      }
      env {
        name  = "PAG_SOURCE_URL"
        value = var.source_url
      }
      env {
        name  = "PAG_GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "PAG_VERTEX_LOCATION"
        value = var.vertex_location
      }
      env {
        name  = "PAG_PROVIDER_HOST_ALLOWLIST"
        value = "${var.vertex_location}-aiplatform.googleapis.com"
      }
      env {
        name  = "PAG_OIDC_ISSUER"
        value = var.oidc_issuer
      }
      env {
        name  = "PAG_OIDC_AUDIENCE"
        value = var.oidc_audience
      }
      env {
        name = "PAG_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = google_secret_manager_secret_version.database_url.version
          }
        }
      }
      env {
        name = "PAG_CACHE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.cache_url.secret_id
            version = google_secret_manager_secret_version.cache_url.version
          }
        }
      }
      env {
        name = "PAG_API_KEY_PEPPER"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.api_key_pepper.secret_id
            version = google_secret_manager_secret_version.api_key_pepper.version
          }
        }
      }
      dynamic "env" {
        for_each = [1]
        content {
          name = "PAG_OIDC_JWKS_JSON"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.oidc_jwks.secret_id
              version = google_secret_manager_secret_version.oidc_jwks.version
            }
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 2
        period_seconds        = 2
        failure_threshold     = 15
        http_get {
          path = "/health"
          port = 8080
        }
      }

      liveness_probe {
        initial_delay_seconds = 10
        timeout_seconds       = 2
        period_seconds        = 10
        failure_threshold     = 3
        http_get {
          path = "/health"
          port = 8080
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_secret_manager_secret_iam_member.shared,
    google_secret_manager_secret_version.api_key_pepper,
    google_secret_manager_secret_version.cache_ca,
    google_secret_manager_secret_version.cache_url,
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_version.gateway_config,
    google_secret_manager_secret_version.oidc_jwks,
  ]
}

resource "google_cloud_run_v2_service" "control_plane" {
  name                = "${var.name}-control-plane"
  location            = var.region
  deletion_protection = false
  ingress             = var.control_plane_ingress
  labels              = local.labels

  template {
    service_account                  = google_service_account.control_plane.email
    timeout                          = "300s"
    max_instance_request_concurrency = 40

    scaling {
      min_instance_count = var.control_plane_min_instances
      max_instance_count = var.control_plane_max_instances
    }

    vpc_access {
      connector = google_vpc_access_connector.gateway.id
      egress    = "PRIVATE_RANGES_ONLY"
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.gateway.connection_name]
      }
    }

    volumes {
      name = "gateway-config"
      secret {
        secret = google_secret_manager_secret.gateway_config.secret_id
        items {
          version = google_secret_manager_secret_version.gateway_config.version
          path    = "config.yaml"
          mode    = 292
        }
      }
    }

    volumes {
      name = "cache-ca"
      secret {
        secret = google_secret_manager_secret.cache_ca.secret_id
        items {
          version = google_secret_manager_secret_version.cache_ca.version
          path    = "ca.pem"
          mode    = 292
        }
      }
    }

    containers {
      image = var.image
      args  = ["control-plane", "--host", "0.0.0.0", "--port", "8080"]

      ports {
        name           = "http1"
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      volume_mounts {
        name       = "gateway-config"
        mount_path = "/etc/portable-ai-gateway"
      }
      volume_mounts {
        name       = "cache-ca"
        mount_path = "/var/run/secrets/cache"
      }

      env {
        name  = "PAG_APP_ENV"
        value = "production"
      }
      env {
        name  = "PAG_CONFIG_PATH"
        value = "/etc/portable-ai-gateway/config.yaml"
      }
      env {
        name  = "PAG_CONFIG_VERSION"
        value = "terraform-gcp-v1alpha1"
      }
      env {
        name  = "PAG_GITOPS_MODE"
        value = "true"
      }
      env {
        name  = "PAG_SOURCE_URL"
        value = var.source_url
      }
      env {
        name  = "PAG_PROVIDER_HOST_ALLOWLIST"
        value = "${var.vertex_location}-aiplatform.googleapis.com"
      }
      env {
        name  = "PAG_OIDC_ISSUER"
        value = var.oidc_issuer
      }
      env {
        name  = "PAG_OIDC_AUDIENCE"
        value = var.oidc_audience
      }
      env {
        name = "PAG_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = google_secret_manager_secret_version.database_url.version
          }
        }
      }
      env {
        name = "PAG_CACHE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.cache_url.secret_id
            version = google_secret_manager_secret_version.cache_url.version
          }
        }
      }
      env {
        name = "PAG_API_KEY_PEPPER"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.api_key_pepper.secret_id
            version = google_secret_manager_secret_version.api_key_pepper.version
          }
        }
      }
      dynamic "env" {
        for_each = [1]
        content {
          name = "PAG_OIDC_JWKS_JSON"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.oidc_jwks.secret_id
              version = google_secret_manager_secret_version.oidc_jwks.version
            }
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 2
        period_seconds        = 2
        failure_threshold     = 15
        http_get {
          path = "/health"
          port = 8080
        }
      }

      liveness_probe {
        initial_delay_seconds = 10
        timeout_seconds       = 2
        period_seconds        = 10
        failure_threshold     = 3
        http_get {
          path = "/health"
          port = 8080
        }
      }
    }
  }

  depends_on = [
    google_project_service.required,
    google_secret_manager_secret_iam_member.shared,
    google_secret_manager_secret_version.api_key_pepper,
    google_secret_manager_secret_version.cache_ca,
    google_secret_manager_secret_version.cache_url,
    google_secret_manager_secret_version.database_url,
    google_secret_manager_secret_version.gateway_config,
    google_secret_manager_secret_version.oidc_jwks,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "data_invoker" {
  for_each = var.data_plane_invoker_members

  project  = var.project_id
  location = google_cloud_run_v2_service.data_plane.location
  name     = google_cloud_run_v2_service.data_plane.name
  role     = "roles/run.invoker"
  member   = each.value
}

resource "google_cloud_run_v2_service_iam_member" "control_invoker" {
  for_each = var.control_plane_invoker_members

  project  = var.project_id
  location = google_cloud_run_v2_service.control_plane.location
  name     = google_cloud_run_v2_service.control_plane.name
  role     = "roles/run.invoker"
  member   = each.value
}
