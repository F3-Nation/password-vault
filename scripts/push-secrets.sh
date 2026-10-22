#!/usr/bin/env bash
# Pushes the four Secret Manager values the Cloud Run job reads (the containers
# themselves are created by OpenTofu; values never live in Terraform state).
#
# Values come from the environment, falling back to ./.env:
#   F3_API_KEY                - from https://admin.f3nation.com/api-keys
#   VAULTWARDEN_CLIENT_ID     - Vaultwarden user API key client id
#   VAULTWARDEN_CLIENT_SECRET - Vaultwarden user API key client secret
#   VAULTWARDEN_ORG_KEY       - base64 org key from scripts/extract_org_key.py
#
# Usage:
#   ./scripts/push-secrets.sh                      # pushes whatever is set
#   F3_API_KEY=... ./scripts/push-secrets.sh --project f3-passwords
#   VAULTWARDEN_ORG_KEY="$(uv run python scripts/extract_org_key.py ... | ...)"
#
# Existing secrets get a new version; missing ones are created.
set -euo pipefail

PROJECT="${PROJECT:-f3-passwords}"
SECRETS=(f3-api-key vaultwarden-client-id vaultwarden-client-secret vaultwarden-org-key)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,15p' "$0"
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

# Pull a value from the environment, or from ./.env when unset there.
load_from_env_file() {
  local name="$1" line value
  [[ -n "${!name:-}" ]] && return 0
  [[ -f .env ]] || return 0
  line="$(grep -E "^${name}=" .env | head -n 1 || true)"
  [[ -n "$line" ]] || return 0
  value="${line#*=}"
  # Strip matching surrounding quotes and a trailing inline comment.
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  value="${value%%[[:space:]]#*}"
  printf -v "$name" '%s' "$value"
  export "$name"
}

for name in F3_API_KEY VAULTWARDEN_CLIENT_ID VAULTWARDEN_CLIENT_SECRET VAULTWARDEN_ORG_KEY; do
  load_from_env_file "$name"
done

push_secret() {
  local name="$1" value="$2"
  if gcloud secrets describe "${name}" --project "${PROJECT}" >/dev/null 2>&1; then
    printf '%s' "${value}" | gcloud secrets versions add "${name}" --data-file=- --project "${PROJECT}"
    echo "updated: ${name}" >&2
  else
    printf '%s' "${value}" | gcloud secrets create "${name}" --data-file=- --project "${PROJECT}"
    echo "created: ${name}" >&2
  fi
}

missing=0
declare -A values=(
  [f3-api-key]="${F3_API_KEY:-}"
  [vaultwarden-client-id]="${VAULTWARDEN_CLIENT_ID:-}"
  [vaultwarden-client-secret]="${VAULTWARDEN_CLIENT_SECRET:-}"
  [vaultwarden-org-key]="${VAULTWARDEN_ORG_KEY:-}"
)

for name in "${SECRETS[@]}"; do
  value="${values[$name]:-}"
  if [[ -z "${value}" ]]; then
    if [[ "${name}" == "vaultwarden-org-key" ]]; then
      # Not fatal here: the users task runs without it, and extract_org_key.py
      # can push it directly with --apply-secret.
      echo "skip: ${name} (no VAULTWARDEN_ORG_KEY value; run scripts/extract_org_key.py)" >&2
    else
      echo "error: no value for ${name}" >&2
      missing=1
    fi
    continue
  fi
  push_secret "${name}" "${value}"
done

if [[ "${missing}" -ne 0 ]]; then
  echo "Set the missing value(s) in the environment or in .env and re-run." >&2
  exit 1
fi
