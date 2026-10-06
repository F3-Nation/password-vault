# Vaultwarden on GCP Cloud Run Setup Guide

This guide walks through deploying a production-ready, low-maintenance [Vaultwarden](https://github.com/dani-garcia/vaultwarden) instance using Google Cloud Run and Google Cloud Storage (GCS).

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

# Enable Cloud Run, Artifact Registry, Storage, and Secret Manager APIs
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com

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

To pull the newest image patch and deploy it with zero downtime, re-run the deployment command:

```bash
gcloud run deploy f3-passwords \
  --image=docker.io/vaultwarden/server:latest \
  --region=us-central1

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