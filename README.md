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
Copy the URL output by `gcloud` (e.g., `[https://f3-passwords-xyz-uc.a.run.app](https://f3-passwords-xyz-uc.a.run.app)`).
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


