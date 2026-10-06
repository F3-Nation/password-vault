#!/usr/bin/env bash
#
# push_template.sh
#
# Uploads the F3-branded Vaultwarden organization-invite email templates to the
# Cloud Storage bucket that backs the Vaultwarden /data volume.
#
# Vaultwarden loads template overrides from "<DATA_FOLDER>/templates" on startup.
# Our deployment mounts gs://f3-passwords-vault-data at /data, so the override
# files must live in the bucket under "templates/email/".
#
# Usage (run from the repository root):
#   ./templates/invite_email/push_template.sh              # upload templates only
#   ./templates/invite_email/push_template.sh --redeploy   # upload, then roll a new Cloud Run revision
#
# Environment overrides (defaults match README.md):
#   VAULT_DATA_BUCKET   gs://f3-passwords-vault-data
#   CLOUD_RUN_PROJECT   f3-passwords
#   CLOUD_RUN_SERVICE   f3-passwords
#   CLOUD_RUN_REGION    us-central1
#
set -euo pipefail

BUCKET="${VAULT_DATA_BUCKET:-gs://f3-passwords-vault-data}"
PROJECT="${CLOUD_RUN_PROJECT:-f3-passwords}"
SERVICE="${CLOUD_RUN_SERVICE:-f3-passwords}"
REGION="${CLOUD_RUN_REGION:-us-central1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${BUCKET}/templates/email"

REDEPLOY=false
if [[ "${1:-}" == "--redeploy" ]]; then
  REDEPLOY=true
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "ERROR: gcloud CLI not found. Install it or run from Cloud Shell." >&2
  exit 1
fi

echo "Uploading invite-email templates to ${DEST}/ ..."
gcloud storage cp "${SCRIPT_DIR}/send_org_invite.html.hbs" "${DEST}/send_org_invite.html.hbs"
gcloud storage cp "${SCRIPT_DIR}/send_org_invite.hbs"      "${DEST}/send_org_invite.hbs"
echo "Upload complete."

if [[ "${REDEPLOY}" == "true" ]]; then
  # Vaultwarden only reads templates at startup, so a new Cloud Run revision is
  # required. "gcloud run deploy" always creates a new revision (unlike
  # "services update", which rejects a no-op change) WITHOUT adding any config or
  # env vars. We redeploy the exact image digest that is currently running, so
  # the restart does not change the Vaultwarden version.
  echo "Resolving the currently-deployed image ..."
  CURRENT_REV="$(gcloud run services describe "${SERVICE}" --project="${PROJECT}" --region="${REGION}" \
    --format='value(status.latestReadyRevisionName)')"
  CURRENT_IMAGE="$(gcloud run revisions describe "${CURRENT_REV}" --project="${PROJECT}" --region="${REGION}" \
    --format='value(spec.containers[0].image)')"
  echo "Rolling a new revision from ${CURRENT_IMAGE} so Vaultwarden reloads templates ..."
  gcloud run deploy "${SERVICE}" --project="${PROJECT}" --region="${REGION}" --image="${CURRENT_IMAGE}"
  echo "Redeploy complete."
else
  echo
  echo "NOTE: Vaultwarden loads templates at startup. Create a new revision to apply"
  echo "      by re-deploying the image that is already running (no version change):"
  echo "  IMG=\$(gcloud run services describe ${SERVICE} --project=${PROJECT} --region=${REGION} \\"
  echo "    --format='value(status.latestReadyRevisionName)' \\"
  echo "    | xargs -I{} gcloud run revisions describe {} --project=${PROJECT} --region=${REGION} \\"
  echo "        --format='value(spec.containers[0].image)')"
  echo "  gcloud run deploy ${SERVICE} --project=${PROJECT} --region=${REGION} --image=\"\$IMG\""
  echo "  (or re-run this script with --redeploy)"
fi
