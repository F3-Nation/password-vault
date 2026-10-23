variable "project_id" {
  description = "GCP project that hosts the Vaultwarden service."
  type        = string
  default     = "f3-passwords"
}

variable "region" {
  description = "Region for the Vaultwarden Cloud Run service."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name for Vaultwarden."
  type        = string
  default     = "f3-passwords"
}

variable "image" {
  description = <<-EOT
    Fully-qualified Vaultwarden image (mirror.gcr.io/vaultwarden/server@sha256:...).
    Renovate keeps the digest current via a regex custom manager in renovate.json;
    a merged bump flows through release-please -> vault@<version> tag -> deploy-vault.yml,
    which applies this root. Pinning by digest (not a mutable tag) keeps every
    deploy reproducible and auditable. The mirror.gcr.io registry avoids Docker Hub
    rate limits and serves byte-identical digests to docker.io.
  EOT
  type        = string
  default     = "mirror.gcr.io/vaultwarden/server@sha256:4ecafc9049c7d878c7717d1ce4f9059d706758c78b8fa42e5ead21f4b2dfc770"
}

variable "data_bucket" {
  description = "GCS bucket Vaultwarden mounts at /data (holds db.sqlite3, config.json, rsa_key.pem). Referenced, not created here."
  type        = string
  default     = "f3-passwords-vault-data"
}

variable "admin_token_secret" {
  description = "Secret Manager secret id holding the Vaultwarden ADMIN_TOKEN (referenced by name; Terraform never reads its value)."
  type        = string
  default     = "ADMIN_TOKEN"
}

variable "runtime_service_account" {
  description = "Service account the Vaultwarden container runs as."
  type        = string
  default     = "416374972780-compute@developer.gserviceaccount.com"
}

variable "max_instances" {
  description = <<-EOT
    Autoscaling upper cap. SQLite on a GCS FUSE mount tolerates only a single
    writer, so this is capped at 1 (min stays 0). Raising it invites concurrent
    write corruption of db.sqlite3.
  EOT
  type        = number
  default     = 1
}
