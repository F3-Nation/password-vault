variable "project_id" {
  description = "GCP project that hosts the sync job."
  type        = string
  default     = "f3-passwords"
}

variable "region" {
  description = "Region for the Cloud Run job, Artifact Registry repo, and schedulers."
  type        = string
  default     = "us-central1"
}

variable "ar_repo" {
  description = "Artifact Registry repository id for the job image."
  type        = string
  default     = "password-vault-sync"
}

variable "job_name" {
  description = "Cloud Run job name (schedulers are named after it too)."
  type        = string
  default     = "password-vault-sync"
}

variable "image" {
  description = <<-EOT
    Fully-qualified image reference (registry/repo:tag or registry/repo@sha256:...).
    Leave empty to default to :latest in this project's Artifact Registry; the
    deploy workflow passes an immutable digest on every release. The referenced
    image must already exist before the job resource is created.
  EOT
  type        = string
  default     = ""
}

variable "vaultwarden_url" {
  description = "Vaultwarden base URL."
  type        = string
  default     = "https://passwords.f3nation.com"
}

variable "vaultwarden_org_id" {
  description = "Vaultwarden organization that collections are managed in."
  type        = string

  validation {
    condition     = length(var.vaultwarden_org_id) > 0
    error_message = "vaultwarden_org_id must be set (it is the organization the sync manages)."
  }
}

variable "f3_api_url" {
  description = "F3 Nation API base URL."
  type        = string
  default     = "https://api.f3nation.com"
}

variable "f3_api_client" {
  description = "Value of the `Client` header sent to the F3 API."
  type        = string
  default     = "password-vault-sync"
}

variable "org_sync_schedule" {
  description = "Cron for the org -> collection sync (one Cloud Scheduler per task)."
  type        = string
  default     = "0 6 * * *"
}

variable "user_sync_schedule" {
  description = "Cron for the role -> collection membership sync."
  type        = string
  default     = "0 * * * *"
}

variable "time_zone" {
  description = "tz-database time zone used to interpret both schedules."
  type        = string
  default     = "America/Chicago"
}

variable "task_timeout" {
  description = "Max runtime per task attempt before Cloud Run kills it."
  type        = string
  default     = "600s"
}
