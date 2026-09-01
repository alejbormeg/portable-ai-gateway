# SPDX-License-Identifier: AGPL-3.0-only
output "data_plane_url" {
  description = "Cloud Run data-plane URL. Invocation still requires IAM unless explicitly granted."
  value       = google_cloud_run_v2_service.data_plane.uri
}

output "control_plane_url" {
  description = "Cloud Run control-plane URL. Invocation still requires IAM unless explicitly granted."
  value       = google_cloud_run_v2_service.control_plane.uri
}

output "data_plane_service_account" {
  value = google_service_account.data_plane.email
}

output "control_plane_service_account" {
  value = google_service_account.control_plane.email
}

output "cloud_sql_connection_name" {
  value = google_sql_database_instance.gateway.connection_name
}

output "cache_host" {
  value     = google_redis_instance.gateway.host
  sensitive = true
}

output "enabled_apis" {
  value = sort(tolist(local.required_services))
}
