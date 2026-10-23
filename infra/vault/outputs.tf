output "service_name" {
  description = "Vaultwarden Cloud Run service name."
  value       = google_cloud_run_v2_service.vaultwarden.name
}

output "service_uri" {
  description = "HTTPS URL of the Vaultwarden service."
  value       = google_cloud_run_v2_service.vaultwarden.uri
}

output "image" {
  description = "Image reference the service is currently configured to run (Renovate-managed digest)."
  value       = var.image
}
