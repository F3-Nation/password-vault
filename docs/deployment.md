# Deployment

How code in this repo reaches production: a Cloud Run job in GCP project
`f3-passwords` (region `us-central1`) that Cloud Scheduler runs twice a day/hour.
Everything past a merge is automated.

## The pipeline at a glance

```
conventional commit  ──►  release-please opens/updates a release PR
        │                          │
        │                 you merge the release PR
        │                          │
        │                 tag v* is created ──► deploy.yml
        │                                         │
        │                     ┌───────────────────┴───────────────────┐
        │                     │ ci-gate: wait for lint/format/test    │
        │                     │ build:   docker build → push to       │
        │                     │          Artifact Registry → resolve  │
        │                     │          the immutable digest         │
        │                     │ deploy:  tofu apply -var image=       │
        │                     │          …:1.2.3@sha256:<digest>      │
        │                     └───────────────────────────────────────┘
        │
  Cloud Scheduler (orgs daily, users hourly)
        └──► Run Admin API jobs:run ──► job runs exactly that digest
```

## 1. Releases (release-please)

Every commit on `main` must be a **conventional commit**
(`type(scope): subject`) — enforced locally by the pre-commit `commit-msg`
hook and in CI by `commitlint.yml` (rules live in
`scripts/check_commit_msg.py`, scopes: `main, sync, infra, ci, scripts,
docs, deps, repo, release`).

- `feat` → minor bump, `fix` → patch, `feat!:`/breaking → major.
- `chore`, `docs`, `ci`, `style`, `test`, `build`, `refactor` are hidden in
  `release-please-config.json`: they land without cutting a release.
- `.release-please-manifest.json` holds the current version (`1.0.0`).

On each push to `main`, `.github/workflows/release-please.yml` mints a
GitHub App token (org secrets `RELEASE_PLEASE_APP_ID` +
`RELEASE_PLEASE_APP_PRIVATE_KEY`, App installed on this repo) and runs
release-please. If there are releasable commits it opens/updates a **release
PR** (version bump + `CHANGELOG.md` entries). Merging that PR creates the
`v*` tag **with the App token** — that matters: a tag pushed with the
default `GITHUB_TOKEN` would *not* trigger `deploy.yml`, so the App is what
closes the loop.

## 2. Deploy (`.github/workflows/deploy.yml`)

Triggered by any pushed `v*` tag; deploys are serialized
(`concurrency: deploy-password-vault-sync`, no cancellation, so two releases
close together queue instead of racing on the same job).

| Job | What it does |
| --- | --- |
| `ci-gate` | Verifies the ref really is a release tag, then waits for `lint`, `format-check`, `test` on the same SHA to pass. |
| `build` | Auth via Workload Identity Federation (`WIF_PROVIDER` + `WIF_SA` repo vars — no JSON keys anywhere), `docker build --platform=linux/amd64`, push to Artifact Registry, resolve the digest for the exact version tag (fails unless exactly one `sha256:` digest matches). |
| `deploy` | `tofu init` against the GCS state bucket (`TF_STATE_BUCKET`), then `tofu apply -var image=<repo>:<version>@sha256:<digest>` — only the job's image changes; everything else is already in state. |

The job ends up **digest-pinned**: `…password-vault-sync:1.1.0@sha256:…`.
Tags in Artifact Registry are mutable; digests are not, so every release is
reproducible and auditable (`gcloud artifacts docker images list …` shows
which tag produced which digest). The `:latest` fallback in
`infra/variables.tf` exists only for the one-time bootstrap.

The tag `vX.Y.Z` becomes the image tag `X.Y.Z` (the `v` is stripped).

## 3. Running (Cloud Scheduler)

Two schedulers start the job; nothing else invokes it on a schedule:

| Scheduler | Cron (default) | Args override |
| --- | --- | --- |
| `password-vault-sync-orgs` | `0 6 * * *` (daily 06:00) | `--task orgs` |
| `password-vault-sync-users` | `0 * * * *` (hourly) | `--task users` |

Both interpret cron in `time_zone` (default `America/Chicago`); a manual
`gcloud run jobs execute` uses the job's default args, `--task all`.

Mechanics worth knowing when debugging:

- Each scheduler POSTs to the Run Admin **v2** `jobs:run` endpoint with a
  base64 body whose `overrides.containerOverrides[0].args` replace the
  container args **for that execution only** — the job template stays
  `--task all`.
- `jobs:run` is asynchronous: the HTTP call returns as soon as the
  execution is created. Success of the call ≠ success of the sync — check
  the execution's logs.
- Auth is an OAuth token for the scheduler SA
  (`password-vault-sync-scheduler@…`), which holds only the custom role
  `passwordVaultSyncJobRunner` = permission `run.jobs.run`. It cannot read
  secrets, images, or anything else.
- `attempt_deadline = 600s`, one retry with 10s backoff; Cloud Run kills a
  single attempt at `task_timeout` (default `600s`).

Schedules, timezone, and timeout are plain inputs in
`infra/variables.tf` — changing them is an infra change (see §5).

## 4. Configuration & secrets

**Repo variables** (Settings → Secrets and variables → Variables):

| Variable | Used by |
| --- | --- |
| `WIF_PROVIDER` / `WIF_SA` | OIDC federation for `build` + `deploy` (org-level values shared across F3 repos) |
| `TF_STATE_BUCKET` | GCS bucket holding `infra/` state |
| `VAULTWARDEN_ORG_ID` | passed as `TF_VAR_vaultwarden_org_id` on apply |

**GitHub secrets**: `RELEASE_PLEASE_APP_ID` + `RELEASE_PLEASE_APP_PRIVATE_KEY`
(org secrets; same GitHub App as `f3-nation`, installed on both repos).

**GCP secrets** (Secret Manager, *created* by OpenTofu, *filled* by
`scripts/push-secrets.sh` so no secret material ever lands in Terraform
state): `f3-api-key`, `vaultwarden-client-id`, `vaultwarden-client-secret`,
`vaultwarden-org-key`. The job binds them to `F3_API_KEY`,
`VAULTWARDEN_CLIENT_ID`, `VAULTWARDEN_CLIENT_SECRET`,
`VAULTWARDEN_ORG_KEY`. Non-secret config (`VAULTWARDEN_URL`,
`VAULTWARDEN_ORG_ID`, …) comes from `infra/main.tf` directly.

**Credentials flow**: GitHub → WIF → short-lived SA token → GCP. There are
no long-lived keys in this repo or its CI.

## 5. Infrastructure changes

`infra/` is OpenTofu; state lives in versioned GCS (only
`.terraform.lock.hcl` is committed, never `.terraform/`).

- **Deployed automatically** on the next release: any `infra/*.tf` diff rides
  along with the tag deploy's `tofu apply`.
- **Deploy now** (infra-only change, no app change): apply locally —

  ```bash
  cd infra
  tofu init -backend-config="bucket=$TF_STATE_BUCKET"
  TF_VAR_vaultwarden_org_id=<org-id> tofu apply
  ```

  or cut a release anyway; the apply is a no-op apart from your diff.

Secret **versions** are pushed out-of-band: `./scripts/push-secrets.sh`
(after editing `.env`). Rotating a secret is "push a new version" — no
redeploy needed, but running executions only pick it up next start.

## 6. Manual operations

```bash
# run the job right now (default args --task all)
gcloud run jobs execute password-vault-sync --region us-central1 --project f3-passwords

# watch an execution / read summaries
gcloud run jobs executions list --job password-vault-sync --region us-central1
gcloud logging read 'resource.type="cloud_run_job" AND textPayload:"summary"' \
  --project f3-passwords --limit=5 --format='value(textPayload)'

# watch a release/deploy in CI
gh run watch --repo F3-Nation/password-vault          # or a specific run id
gh run rerun <id> --repo F3-Nation/password-vault --failed   # retry a flake
```

**Rollback** = point the job at a previous release's digest (find them with
`gcloud artifacts docker images list` in the `password-vault-sync` repo):

```bash
gcloud run jobs update password-vault-sync --region us-central1 \
  --image us-central1-docker.pkg.dev/f3-passwords/password-vault-sync/password-vault-sync:1.0.0@sha256:<old-digest>
```

(or set `var.image` and apply). The next tag deploy will simply pin forward
again.

## 7. Troubleshooting

| Symptom | Where to look |
| --- | --- |
| Deploy run red | Which job failed: `ci-gate` (CI never passed / not a real tag), `build` (WIF auth, AR permissions), `deploy` (`tofu apply` output — e.g. a disabled GCP API surfaces as `SERVICE_DISABLED`). Re-run with `gh run rerun <id> --failed` after fixing. |
| No release PR appearing | Check the **Release Please** workflow run: "No user facing commits found" means only hidden types (`chore`/`docs`/`ci`) landed — intended. Failures at `create-github-app-token` mean the App secrets are missing/not visible to this repo. |
| Release PR exists but no tag after merge | Merging the PR only pushes to `main`; the *next* Release Please run (triggered by that push) creates the tag with the App token. If no `v*` tag appears, inspect that run. A tag created any other way (e.g. a manual `git push origin v*`) also works for deploys but bypasses release-please's bookkeeping. |
| Scheduler fired but no sync happened | `gcloud scheduler jobs list` → confirm RUNNABLE; then find the execution the call created and read its logs — the API call itself returns success immediately. |
| Sync ran but made wrong changes | Always re-run with `--dry-run` locally first: `uv run python -m password_vault_sync --task users --dry-run --verbose`. |
