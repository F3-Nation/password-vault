# ------------------------------------------------------------------------------
# Vaultwarden (the password vault itself)
#
# A single Cloud Run service running the upstream vaultwarden/server image,
# autoscaling 0 -> 1 (SQLite on a GCS FUSE mount tolerates only one writer, so
# max instances is capped at 1). The data lives in a GCS bucket mounted at /data
# and the ADMIN_TOKEN comes from Secret Manager; both predate terraform and hold
# real data, so they are referenced here, never created.
#
# The image is maintained by Renovate (regex custom manager in renovate.json).
# A merged Renovate bump flows through release-please -> vault@<version> tag ->
# deploy-vault.yml, which applies this root -- so an image update deploys on the
# next vault release, independent of the sync job.
# ------------------------------------------------------------------------------

resource "google_cloud_run_v2_service" "vaultwarden" {
  project             = var.project_id
  name                = var.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = true

  template {
    service_account = var.runtime_service_account

    # Max instances (1). Expressed as the Knative autoscaling annotation because
    # the v2 schema has no typed field for it. CPU throttling, session affinity
    # and the startup boost use their typed fields instead (see below).
    annotations = {
      "autoscaling.knative.dev/maxScale" = tostring(var.max_instances)
    }

    # No sticky sessions: with a single instance any request can be served by it.
    session_affinity = false

    # Persistent data: a GCS FUSE volume (bucket -> /data). Versioning on the
    # bucket is the real safety net for db.sqlite3.
    volumes {
      name = "vault-data"
      gcs {
        bucket = var.data_bucket
      }
    }

    containers {
      image = var.image

      ports {
        container_port = 80
        name           = "http1"
      }

      env {
        name  = "DATA_FOLDER"
        value = "/data"
      }
      env {
        name  = "ORG_EVENTS_ENABLED"
        value = "true"
      }
      env {
        name  = "EVENTS_DAYS_RETAIN"
        value = "190"
      }
      env {
        name  = "CLIENT_SUPPRESS_ONBOARDING"
        value = "true"
      }
      env {
        name = "ADMIN_TOKEN"
        value_source {
          secret_key_ref {
            secret  = var.admin_token_secret
            version = "latest"
          }
        }
      }

      resources {
        limits = {
          cpu    = "1000m"
          memory = "512Mi"
        }
        # CPU is throttled outside request handling (the Cloud Run default);
        # a startup boost shortens the (already long) SQLite-on-GCS cold start.
        cpu_idle          = true
        startup_cpu_boost = true
      }

      volume_mounts {
        name       = "vault-data"
        mount_path = "/data"
      }

      # SQLite on GCS FUSE can be slow to open; give a long TCP startup probe so
      # a cold start isn't killed before the container is listening.
      startup_probe {
        tcp_socket {
          port = 80
        }
        period_seconds    = 240
        timeout_seconds   = 240
        failure_threshold = 1
      }
    }

    timeout                          = "300s"
    max_instance_request_concurrency = 80
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  # The provider drops the Knative autoscaling annotation (maxScale) during
  # refresh, so without this the plan would show a never-clearing cosmetic
  # "add" diff. The annotation above still applies on a fresh create; ignoring
  # it on update keeps `tofu plan` clean so real changes stand out. The max
  # instances cap of 1 is intentional (SQLite on GCS FUSE allows one writer).
  lifecycle {
    ignore_changes = [template[0].annotations]
  }
}
