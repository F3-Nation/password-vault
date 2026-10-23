# Password Vault Sync (F3 Nation API → Vaultwarden)

Syncs a self-hosted [Vaultwarden](https://github.com/dani-garcia/vaultwarden)
instance from the [F3 Nation API](https://api.f3nation.com/docs) as a **single
parameterized GCP Cloud Run Job**:

| Task | Triggered by | What it does |
| --- | --- | --- |
| `--task orgs` | Cloud Scheduler, daily 06:00 America/Chicago | F3 region orgs with `meta.password_vault_enabled === true` become collections (created if missing, renamed when the org is renamed). Orgs that turn the flag off keep their collection but lose every user grant on it. |
| `--task users` | Cloud Scheduler, hourly | `password_manager` → read/write, `password_reader` → read-only (both → read/write) on the collection for that role's org. Users without access stay org members with **zero** collection grants. Owners/admins (`accessAll`) and revoked members are never touched. |
| `--task all` | manual runs / job default | `orgs` first, then `users`. |

Both tasks only ever *reconcile*: they never delete collections, never touch
`accessAll`, and never re-grant access to an org whose vault is disabled.

Collection **names** are encrypted client-side with the organization key
(Bitwarden format `2.<iv>|<data>|<mac>`, AES-256-CBC + HMAC-SHA256), so the
job needs that key — extracted **once**, locally, with your master password
(`scripts/extract_org_key.py`), and stored in Secret Manager. The master
password itself never runs in the cloud and is never stored.

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/password_vault_sync/` | The job: crypto, F3 + Vaultwarden clients, both sync tasks, CLI |
| `tests/` | Pytest suite (crypto vectors, pagination, both sync rule sets, CLI) |
| `scripts/extract_org_key.py` | One-time local org-key extraction + verification |
| `scripts/push-secrets.sh` | Pushes the four Secret Manager values |
| `scripts/check_commit_msg.py` | Conventional-commit rules (shared by the pre-commit hook and CI) |
| `infra/` | OpenTofu: Artifact Registry, Cloud Run job, 2 schedulers, SAs, secrets |
| `.github/workflows/` | `ci.yml`, `commitlint.yml`, `release-please.yml`, `release-please-uv-lock.yml`, `deploy.yml` |
| `Dockerfile` | Slim Python 3.13 + uv image, non-root, `ENTRYPOINT python -m password_vault_sync` |
| `README.md` (Part 2) | The original guide to hosting Vaultwarden itself on Cloud Run |

## Local development

```bash
uv sync --locked          # install deps (Python from .python-version)
uv run ruff check .       # lint
uv run ruff format .      # format
uv run pytest             # full suite, no network needed

# Enforce Conventional Commits at commit time (once per clone):
uv run pre-commit install --hook-type commit-msg

# Run against the live instance using ./.env (dry-run writes nothing):
uv run python -m password_vault_sync --task users --dry-run --verbose
uv run python -m password_vault_sync --task orgs --dry-run
```

## Configuration

Environment variables (in Cloud Run they are bound from Secret Manager by
`infra/main.tf`; locally they live in `.env`, see `.env.example`):

| Variable | Secret? | Notes |
| --- | --- | --- |
| `F3_API_KEY` | yes | From <https://admin.f3nation.com/api-keys>. Sent as `Authorization: Bearer <key>` **plus** a `Client: <identifier>` header — a bare token without `Client` is rejected with 401. |
| `F3_API_URL` | no | Defaults to `https://api.f3nation.com` |
| `F3_API_CLIENT` | no | The `Client` header value, defaults to `password-vault-sync` |
| `VAULTWARDEN_URL` | no | e.g. `https://passwords.f3nation.com` |
| `VAULTWARDEN_ORG_ID` | no | The organization whose collections are managed |
| `VAULTWARDEN_CLIENT_ID` | yes | Vaultwarden **user API key** (`Profile → Account Settings → Security → Keys → View API key`), used with `client_credentials` so the job runs unattended |
| `VAULTWARDEN_CLIENT_SECRET` | yes | Same dialog as the client id |
| `VAULTWARDEN_ORG_KEY` | yes | base64 32-byte org key; **required by `orgs`, ignored by `users`** |

## One-time setup (GCP + GitHub)

Prerequisites: `gcloud` authenticated for project `f3-passwords`, and
OpenTofu ≥ 1.8 (`/tmp/opencode/bin/tofu` locally, or any `tofu`/`terraform`).

1. **Repository variables** (Settings → Secrets and variables → Variables):
   `WIF_PROVIDER`, `WIF_SA` (the workload-identity pair the F3 org already
   uses for Cloud Run deploys) and `TF_STATE_BUCKET` (the existing GCS bucket
   for `infra/` state).
2. **Repository secrets**: `RELEASE_PLEASE_APP_ID`,
   `RELEASE_PLEASE_APP_PRIVATE_KEY` (the GitHub App that tags releases, so its
   tag push can trigger `deploy.yml`).
3. **F3 API key**: create one at <https://admin.f3nation.com/api-keys> and put
   it in `.env` as `F3_API_KEY` (push-secrets.sh picks it up from there).
4. **Extract the org key** (needs your Vaultwarden master password once,
   locally; verifies the key by decrypting existing collection names and
   cross-checking them against the F3 API):

   ```bash
   uv run python scripts/extract_org_key.py --email you@f3nation.com
   # add --apply-secret to also run the gcloud command it prints
   ```

5. **Bootstrap the infrastructure** (order matters: the job resource needs an
   existing image *and* secret versions):

   ```bash
   cd infra
   tofu init -backend-config="bucket=YOUR_TF_STATE_BUCKET"
   tofu apply -target=google_artifact_registry_repository.images \
             -target=google_secret_manager_secret.secrets \
             -target=google_service_account.runtime \
             -target=google_service_account.scheduler
   cd ..

   # push the four secret values (VAULTWARDEN_ORG_KEY from step 4)
   ./scripts/push-secrets.sh

   # push a first image so the job can be created
   docker build --platform=linux/amd64 -t us-central1-docker.pkg.dev/f3-passwords/password-vault-sync/password-vault-sync:latest .
   docker push us-central1-docker.pkg.dev/f3-passwords/password-vault-sync/password-vault-sync:latest

   cd infra && tofu apply && cd ..
   ```

6. **Smoke test**:

   ```bash
   gcloud run jobs execute password-vault-sync --region us-central1 --project f3-passwords
   # job default args are --task all; watch it:
   gcloud logging read 'resource.type="cloud_run_job" AND textPayload:"summary"' \
     --project f3-passwords --limit=5 --format='value(textPayload)'
   ```

## Releases & deploys

Commit messages must be **Conventional Commits** — `type(scope): subject` with
scopes from `main, sync, infra, ci, scripts, docs, deps, repo, release`
(e.g. `fix(sync): stop re-putting invited member grants`). release-please
bumps versions from those types (`feat` → minor, `fix` → patch), so a
non-conventional message never produces a release. The rules live in
`scripts/check_commit_msg.py` and run in three places: a pre-commit
`commit-msg` hook (`uv run pre-commit install --hook-type commit-msg`), and
CI on the PR title and commit subjects (`commitlint.yml`, with release-please's
own bot PRs exempt).

Conventional commits on `main` → **release-please** opens/updates a release PR
(`release-please-config.json`, plain `v*` tags). Merging it creates the tag,
which triggers **`deploy.yml`**: wait for CI → build & push the image tagged
with the release version → resolve its digest → `tofu apply -var image=…@sha256:…`
so the Cloud Run job always runs exactly what that release built.

Schedules, region, and limits live in `infra/variables.tf`
(`org_sync_schedule`, `user_sync_schedule`, `time_zone`, `task_timeout`).

### Troubleshooting

* `gcloud run jobs executions describe <id> --region us-central1` for exit status; logs via
  `gcloud logging read 'resource.type="cloud_run_job"'`.
* `401 … ensure the 'client' header is set` → `F3_API_KEY`/`F3_API_CLIENT` problem.
* `MAC verification failed` or garbage collection names → wrong/stale
  `VAULTWARDEN_ORG_KEY`; re-run `scripts/extract_org_key.py` and push again.
* Task exited `2` → missing environment variable (message names it); exit `1`
  → a per-org/per-member failure was counted (details in the logs).

---

# Part 2: Vaultwarden on GCP Cloud Run Setup Guide

This guide walks through deploying a production-ready, low-maintenance [Vaultwarden](https://github.com/dani-garcia/vaultwarden) instance using Google Cloud Run and Google Cloud Storage (GCS).

> **The Cloud Run service is now managed by OpenTofu** in [`infra/vault/`](infra/vault/) (see `docs/deployment.md`). Its image is kept current by Renovate and deployed via the `vault@*` release pipeline — so the manual `gcloud run deploy` steps below are **reference/history only**; do not re-run them against the live service (they would drift from terraform). Sections on one-time setup (project APIs, the GCS bucket, the admin token, first-run lockdown) and the Bitwarden CLI still apply as-is.

---

## Prerequisites

* A [Google Cloud Platform](https://console.cloud.google.com/) account.
* GCP `gcloud` CLI installed or access to **Cloud Shell**.
* Administrative permissions on the target GCP project.

---

## 1. Project Initialization & API Setup

Set your active GCP project ID and enable the necessary service APIs.

```bash
# Set your active GCP project
gcloud config set project f3-passwords

# Enable Cloud Run, Artifact Registry, Storage, Secret Manager, Scheduler,
# and Cloud Resource Manager (project-level IAM bindings created by OpenTofu)
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com

```

---

## 2. Create Persistent Storage

Vaultwarden requires persistent storage for its SQLite database and user attachments. Create a Google Cloud Storage bucket to act as the backend filesystem via Cloud Storage FUSE.

```bash
gcloud storage buckets create gs://f3-passwords-vault-data \
  --location=us-central1 \
  --uniform-bucket-level-access

```

*(Optional but recommended)* Enable bucket versioning to prevent accidental data loss:

```bash
gcloud storage buckets update gs://f3-passwords-vault-data --versioning

```

---

## 3. Generate and Store Admin Token

The Admin Token grants access to the `/admin` configuration panel. Generate an Argon2 hash of your chosen password and save it to GCP Secret Manager.

1. **Generate the Argon2 hash:**
```bash
docker run --rm -it vaultwarden/server /vaultwarden hash

```


*Enter your desired admin password twice. Copy the entire output string starting with `$argon2id$...`.*
2. **Save the hashed token in Secret Manager:**
```bash
# Wrap the hash in single quotes ('') so the terminal does not parse the '$' symbols
echo -n '$argon2id$v=19$m=65540,t=3,p=1$...' | gcloud secrets create ADMIN_TOKEN --data-file=-

```



---

## 4. Deploy Vaultwarden to Cloud Run

Deploy the official Vaultwarden container image, mount the GCS storage bucket, and bind the admin secret.

```bash
gcloud run deploy f3-passwords \
  --image=docker.io/vaultwarden/server:latest \
  --region=us-central1 \
  --port=80 \
  --allow-unauthenticated \
  --add-volume=name=vault-data,type=cloud-storage,bucket=f3-passwords-vault-data \
  --add-volume-mount=volume=vault-data,mount-path=/data \
  --set-secrets="ADMIN_TOKEN=ADMIN_TOKEN:latest" \
  --set-env-vars="DATA_FOLDER=/data,SIGNUPS_ALLOWED=true,INVITATIONS_ALLOWED=true"

```

---

## 5. First-Time Setup & Security Lockdown

1. **Initial Access:**
Copy the URL output by `gcloud` (e.g., `https://f3-passwords-xyz-uc.a.run.app`).
2. **Register Admin Accounts:**
Navigate to the URL and create the initial user account(s).
3. **Verify Admin Access:**
Navigate to `https://<YOUR-CLOUD-RUN-URL>/admin` and test logging in with the **plain-text password** you used when generating the Argon2 hash in Step 3.
4. **Disable Open Signups:**
Once initial users are registered, turn off public account creation:
```bash
gcloud run services update f3-passwords \
  --region=us-central1 \
  --update-env-vars="SIGNUPS_ALLOWED=false"

```
5. **Enable org level logging, suppress onboarding messaging**
```bash
gcloud run services update f3-passwords \
  --region=us-central1 \
  --update-env-vars="ORG_EVENTS_ENABLED=true,EVENTS_DAYS_RETAIN=190,CLIENT_SUPPRESS_ONBOARDING=true"
```


---

## Maintenance & Upgrades

### Updating to the Latest Vaultwarden Version

Updates are now automatic: **Renovate** opens a `fix(infra):` PR bumping the
digest in [`infra/vault/variables.tf`](infra/vault/variables.tf); merging it
flows through release-please → a `vault@X.Y.Z` tag → `deploy-vault.yml`, which
applies the change (zero-downtime rolling revision). You can also bump by hand:

```bash
cd infra/vault
# point var.image at the digest you want, then
tofu init -backend-config="bucket=$TF_STATE_BUCKET"
tofu apply
```

### Modifying Configuration Settings

Application settings (such as SMTP mail settings, login options, and org policies) can be adjusted in real time by visiting the Web Admin interface at `https://<YOUR-CLOUD-RUN-URL>/admin`. Changes made there persist inside the GCS bucket under `/data/config.json`.

---

## Bitwarden CLI Setup (`bw`)

The `create_region_collection.py` script relies on the Bitwarden CLI (`bw`) to perform client-side AES-256 encryption of collection names before sending them to Vaultwarden, so names render correctly in the Web Vault without decryption errors. (`add_members_to_collection.py` does **not** use the CLI — it talks to the Vaultwarden HTTP API directly.)

---

### 1. Installation

Install the Bitwarden CLI binary on your machine or container environment:

* **macOS (Homebrew):**

  ```bash
  brew install bitwarden-cli
  ```

* **Linux / Cloud Run container (npm):**

  ```bash
  npm install -g @bitwarden/cli
  ```

* **Direct binary download (Linux x64):**

  ```bash
  curl -LO "https://vault.bitwarden.com/download/?app=cli&platform=linux"
  unzip "index.html?app=cli&platform=linux" -d /usr/local/bin/
  chmod +x /usr/local/bin/bw
  ```

---

### 2. Configure Vaultwarden Host

Point the CLI to your self-hosted Vaultwarden server URL:

```bash
bw config server https://passwords.f3nation.com
```

---

### 3. Authentication & Session Export

To run the provisioning scripts, the CLI requires an active authenticated session token (`BW_SESSION`).

#### Method A: Interactive Master Password Login (Recommended for Local Dev)

1. Log in to your Vaultwarden account:

   ```bash
   bw login user@f3nation.com
   ```

2. Unlock your vault and export the session key to your environment:

   ```bash
   export BW_SESSION=$(bw unlock --raw)
   ```

#### Method B: API Key Login (Recommended for Cloud Run / CI Pipelines)

1. Set your personal API credentials (retrieved from Web Vault → **Account Settings → Security → Keys → View API Key**):

   ```bash
   export BW_CLIENTID="user.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
   export BW_CLIENTSECRET="your_user_api_secret"
   ```

2. Authenticate and obtain the raw session token:

   ```bash
   bw login --apikey
   export BW_SESSION=$(bw unlock --raw)
   ```

---

### 4. Verification

Verify that your CLI session is active and linked to the organization:

```bash
# Verify session state
bw status

# Verify Organization ID visibility
bw get template org-collection
```

If `bw status` outputs `"status": "unlocked"`, you are ready to execute the provisioning scripts.

---

## Automation Scripts

The `scripts/` directory contains Python utilities for provisioning collections and managing member access.

| Script | Purpose | Authentication |
|--------|---------|----------------|
| `create_region_collection.py` | Create an organization collection for an F3 region (stores the Postgres region id as the collection's `externalId`). | Bitwarden CLI session (`BW_SESSION`) |
| `add_members_to_collection.py` | Invite and/or grant members access to a collection, and reconcile each member's read-only vs. read/write permission. | Vaultwarden HTTP API (organization API key) |

### Setup

From the repository root, create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file (see `.env.example`):

```bash
VAULTWARDEN_URL=https://passwords.f3nation.com
VAULTWARDEN_CLIENT_ID=user.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
VAULTWARDEN_CLIENT_SECRET=your_user_api_secret_here
VAULTWARDEN_ORG_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# Optional: default members CSV path for add_members_to_collection.py
# MEMBERS_CSV_PATH=/path/to/members.csv
```

The API key must belong to an organization **owner/admin** user so it can invite members and edit collection access. Retrieve it from the Web Vault → **Account Settings → Security → Keys → View API Key**.

### `create_region_collection.py`

Creates an encrypted organization collection. Requires an unlocked `bw` session (see [Bitwarden CLI Setup](#bitwarden-cli-setup-bw) above), because the collection name is encrypted client-side.

```bash
export BW_SESSION=$(bw unlock --raw)
python scripts/create_region_collection.py \
  --region-name "F3 Grand Strand" \
  --postgres-id 42
```

The `--postgres-id` value is stored as the collection's `externalId`, making it the natural join key back to Postgres.

### `add_members_to_collection.py`

Reconciles a collection's membership from a CSV. For each member it will:

- **Invite** the user if they are not yet in the organization (granting the collection as part of the invite).
- **Grant** the collection if the user is already a member but lacks access.
- **Update** the user's permission if their current read-only/read-write status differs from the CSV.
- **Skip** members who already match, who have access to all collections, or who have been revoked.

This script uses the Vaultwarden HTTP API with the organization API key (OAuth `client_credentials`), so it runs fully unattended — no `bw` session or master password required. This makes it suitable for scheduled execution (e.g. a GCP Cloud Run Job).

#### CSV format

A header row with an `email` column and an optional `permission` column. The `permission` cell accepts `rw`/`ro` (and synonyms such as `read`/`write`); blank cells fall back to `--default-permission` (which defaults to `ro`). In the example below, `carol` inherits the default.

```csv
email,permission
alice@f3nation.com,rw
bob@f3nation.com,ro
carol@f3nation.com,
```

#### Usage

Target the collection with **exactly one** of `--collection-uuid` or
`--collection-external-id` (the latter is the region's Postgres id, e.g. `37004`).
Supplying both, or neither, is an error.

```bash
# By externalId (region's Postgres id)
python scripts/add_members_to_collection.py \
  --collection-external-id 37004 \
  --csv ./members.csv \
  --default-permission ro

# ...or by collection UUID
python scripts/add_members_to_collection.py \
  --collection-uuid fa17dcb9-4afa-43bc-858c-e44586de0326 \
  --csv ./members.csv
```

`--org-id`, `--vw-url`, `--client-id`, and `--client-secret` default to the corresponding `.env` values, and `--csv` defaults to `MEMBERS_CSV_PATH`. Members are processed independently; the script exits non-zero if any row fails, so errors surface in job logs without aborting the entire batch.