output "job_name" {
  description = "Cloud Run job name (pass to `gcloud run jobs execute`)."
  value       = google_cloud_run_v2_job.sync.name
}

output "job_location" {
  description = "Region of the Cloud Run job."
  value       = google_cloud_run_v2_job.sync.location
}

output "image" {
  description = "Image reference the job is currently configured to run."
  value       = local.image
}

output "runtime_service_account" {
  description = "Runtime service account of the job (also used by the deploy workflow)."
  value       = google_service_account.runtime.email
}

output "scheduler_service_account" {
  description = "Service account Cloud Scheduler uses to authenticate jobs:run."
  value       = google_service_account.scheduler.email
}

output "artifact_repository" {
  description = "Full Artifact Registry path for images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${var.ar_repo}"
}

output "secret_names" {
  description = "Secret Manager secrets the job reads (fill with scripts/push-secrets.sh)."
  value       = { for name, secret in google_secret_manager_secret.secrets : name => secret.secret_id }
}

output "scheduler_names" {
  description = "Cloud Scheduler jobs, one per sync task."
  value       = { for key, job in google_cloud_scheduler_job.sync : key => job.name }
}
