locals {
  # The deploy workflow passes an immutable digest; bare `tofu apply` falls
  # back to the mutable :latest tag (bootstrap / manual runs only).
  image = var.image != "" ? var.image : "${var.region}-docker.pkg.dev/${var.project_id}/${var.ar_repo}/${var.job_name}:latest"

  # Non-secret configuration, passed to the job as literal env vars.
  job_env = {
    VAULTWARDEN_URL    = var.vaultwarden_url
    VAULTWARDEN_ORG_ID = var.vaultwarden_org_id
    F3_API_URL         = var.f3_api_url
    F3_API_CLIENT      = var.f3_api_client
  }

  # Secret Manager secret ids (Terraform creates the containers only; values
  # are pushed by scripts/push-secrets.sh, so no secret material ever lives in
  # state). What goes in each:
  #   f3-api-key                - F3 API key from https://admin.f3nation.com/api-keys
  #                               (sent as `Authorization: Bearer <key>`)
  #   vaultwarden-client-id     - Vaultwarden user API key client id
  #                               (Profile > Account Settings > Security > Keys > View API key)
  #   vaultwarden-client-secret - Vaultwarden user API key client secret (same dialog)
  #   vaultwarden-org-key       - base64 32-byte org key from scripts/extract_org_key.py
  #                               (encrypts collection names)
  secrets = [
    "f3-api-key",
    "vaultwarden-client-id",
    "vaultwarden-client-secret",
    "vaultwarden-org-key",
  ]

  # Container env var -> secret id (each env var below reads the secret above).
  secret_env = {
    F3_API_KEY                = "f3-api-key"
    VAULTWARDEN_CLIENT_ID     = "vaultwarden-client-id"
    VAULTWARDEN_CLIENT_SECRET = "vaultwarden-client-secret"
    VAULTWARDEN_ORG_KEY       = "vaultwarden-org-key"
  }

  # One Cloud Scheduler per task. Each POSTs to the Run Admin API with the
  # container args overridden in the request body (jobs:run is asynchronous:
  # the HTTP call returns as soon as the execution is created).
  schedules = {
    orgs  = { schedule = var.org_sync_schedule, args = ["--task", "orgs"] }
    users = { schedule = var.user_sync_schedule, args = ["--task", "users"] }
  }
}

# ------------------------------------------------------------------------------
# Identity
# ------------------------------------------------------------------------------

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "${var.job_name}-sa"
  display_name = "Password Vault Sync (Cloud Run job runtime)"
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "${var.job_name}-scheduler"
  display_name = "Password Vault Sync (Cloud Scheduler -> jobs:run)"
}

# Least-privilege custom role: the schedulers may only start this job's
# executions, nothing else in Cloud Run.
resource "google_project_iam_custom_role" "run_job_runner" {
  project     = var.project_id
  role_id     = "passwordVaultSyncJobRunner"
  title       = "Password Vault Sync Job Runner"
  description = "Allows starting executions of the password-vault-sync Cloud Run job"
  permissions = ["run.jobs.run"]
}

resource "google_project_iam_member" "scheduler_runs_job" {
  project = var.project_id
  role    = google_project_iam_custom_role.run_job_runner.id
  member  = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_project_iam_member" "runtime_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# ------------------------------------------------------------------------------
# Image repository
# ------------------------------------------------------------------------------

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.ar_repo
  description   = "Container images for the password-vault-sync Cloud Run job"
  format        = "DOCKER"
}

resource "google_artifact_registry_repository_iam_member" "runtime_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.repository_id
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.runtime.email}"
}

# ------------------------------------------------------------------------------
# Secrets (containers only - versions are added by scripts/push-secrets.sh)
# ------------------------------------------------------------------------------

resource "google_secret_manager_secret" "secrets" {
  project   = var.project_id
  for_each  = toset(local.secrets)
  secret_id = each.value

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "runtime_access" {
  project   = var.project_id
  for_each  = google_secret_manager_secret.secrets
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

# ------------------------------------------------------------------------------
# The job (single job, parameterized with --task orgs|users|all)
# ------------------------------------------------------------------------------

resource "google_cloud_run_v2_job" "sync" {
  project             = var.project_id
  name                = var.job_name
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.runtime.email
      timeout         = var.task_timeout
      max_retries     = 2

      containers {
        image = local.image
        args  = ["--task", "all"] # default for manual runs; schedulers override per task

        dynamic "env" {
          for_each = local.job_env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = local.secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.secrets[env.value].secret_id
                version = "latest"
              }
            }
          }
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }
    }
  }

  # Secrets must have a version (push-secrets.sh) and the image must exist
  # (CI push or a local docker push) before the job can be created.
  depends_on = [
    google_secret_manager_secret_iam_member.runtime_access,
    google_artifact_registry_repository_iam_member.runtime_reader,
  ]
}

# ------------------------------------------------------------------------------
# Schedulers: one per task, both targeting the same job with different args
# ------------------------------------------------------------------------------

resource "google_cloud_scheduler_job" "sync" {
  project          = var.project_id
  for_each         = local.schedules
  name             = "${var.job_name}-${each.key}"
  region           = var.region
  description      = "Runs the password-vault-sync Cloud Run job with --task ${each.key}"
  schedule         = each.value.schedule
  time_zone        = var.time_zone
  attempt_deadline = "600s"

  retry_config {
    retry_count          = 1
    min_backoff_duration = "10s"
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.sync.name}:run"
    headers = {
      "Content-Type" = "application/json"
    }
    # Cloud Scheduler expects the request body base64-encoded; the Run API
    # decodes it and reads the JSON RunJobRequest, whose overrides replace the
    # job template's container args for this execution only.
    body = base64encode(jsonencode({
      overrides = {
        containerOverrides = [
          { args = each.value.args }
        ]
      }
    }))

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }
}
