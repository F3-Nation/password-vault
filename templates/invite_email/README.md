# Invite Email Template (Vaultwarden Org Invite)

This folder customizes the email Vaultwarden sends when a user is invited to an
organization. The goal is to apply **F3 Nation branding** and to make every
invitee **acknowledge the [Disclaimer](../../docs/Disclaimer.md)** — by accepting
the invite they confirm they have read and acknowledge it.

## Files

| File | Purpose |
| --- | --- |
| `send_org_invite.html.hbs` | The HTML email (branding, accept button, disclaimer acknowledgement). Also supplies the email **subject** line. |
| `send_org_invite.hbs` | The plain-text fallback (text-only mail clients) with the same acknowledgement + disclaimer link. |
| `push_template.sh` | Uploads both templates to the Cloud Storage bucket backing Vaultwarden. |
| `README.md` | This file. |

## How it works

Vaultwarden lets you override any built-in template by dropping a replacement in
its templates directory. Two facts drive this setup:

1. `TEMPLATES_FOLDER` defaults to `<DATA_FOLDER>/templates`. Our deployment sets
   `DATA_FOLDER=/data`, so overrides must live in `/data/templates`.
2. The `/data` volume is the Cloud Storage bucket `gs://f3-passwords-vault-data`
   mounted via Cloud Storage FUSE (see the repo [README](../../README.md)).

So the override files must land in the bucket at:

```
gs://f3-passwords-vault-data/templates/email/send_org_invite.html.hbs
gs://f3-passwords-vault-data/templates/email/send_org_invite.hbs
```

A file named `email/send_org_invite.html.hbs` registers as the template
`email/send_org_invite.html`, which is exactly the built-in invite email it
replaces.

### Template contract (don't break these)

- The **first line is the email subject**, followed by a line containing only
  `<!---------------->`, and then the body. Vaultwarden takes the subject from
  the `.html` template's first line, so keep that structure intact.
- Variables available in this template: `{{org_name}}`, `{{{url}}}` (the accept
  link), and `{{img_src}}`. Use the triple-brace form (`{{{url}}}`) for the URL
  so it is not HTML-escaped.
- The HTML is intentionally **self-contained** (its own `<html>`/`<body>`) so the
  email is fully F3-branded and does not render the default Vaultwarden logo.

## Deploy / update the template

From the repository root:

```bash
# Upload the templates to the bucket
./templates/invite_email/push_template.sh

# Upload AND roll a new Cloud Run revision so Vaultwarden reloads them
./templates/invite_email/push_template.sh --redeploy
```

> Vaultwarden loads templates **at startup**, so applying a change requires a new
> Cloud Run revision. `gcloud run deploy` creates one on every run (unlike
> `services update`, which rejects a no-op change) and adds **no config or env
> vars**. The `--redeploy` flag re-deploys the exact image digest already running,
> so the restart does not change the Vaultwarden version. To do it manually:
>
> ```bash
> IMG=$(gcloud run services describe f3-passwords --project=f3-passwords --region=us-central1 \
>   --format='value(status.latestReadyRevisionName)' \
>   | xargs -I{} gcloud run revisions describe {} --project=f3-passwords --region=us-central1 \
>       --format='value(spec.containers[0].image)')
> gcloud run deploy f3-passwords --project=f3-passwords --region=us-central1 --image="$IMG"
> ```
>
> The script passes `--project` explicitly (default `f3-passwords`) so the
> redeploy works even when your active `gcloud config` points at a different
> project. The bucket upload is unaffected by the active project because GCS
> bucket names are global.

Environment overrides (defaults shown):

```bash
VAULT_DATA_BUCKET=gs://f3-passwords-vault-data \
CLOUD_RUN_PROJECT=f3-passwords \
CLOUD_RUN_SERVICE=f3-passwords \
CLOUD_RUN_REGION=us-central1 \
  ./templates/invite_email/push_template.sh --redeploy
```

### Prerequisites

- `gcloud` CLI installed and authenticated (`gcloud auth login`) with access to
  the `f3-passwords` project, or run from Cloud Shell. Your active `gcloud config`
  project does **not** need to be `f3-passwords` — the script targets it via
  `--project`.
- Write access to `gs://f3-passwords-vault-data` and permission to update the
  Cloud Run service.
- **SMTP must be configured** in the Vaultwarden `/admin` panel, otherwise no
  invite emails are sent at all.

## Verify the change

1. After redeploying, invite a test address from the org
   (`/admin` → the organization → invite, or the web vault).
2. Confirm the received email shows the F3 header/colors, the **Accept Invite**
   button, and the disclaimer acknowledgement linking to `docs/Disclaimer.md`.
3. For faster iteration you can temporarily set `RELOAD_TEMPLATES=true` on the
   service (reloads templates every request — slower, dev only) and unset it when
   done.

## Customizing

- **Colors:** The palette uses F3 dark charcoal `#242A2B` and the F3 brand red
  `#ad0c02` (sampled from f3nation.com) with white text. Search those hex values
  in `send_org_invite.html.hbs` to adjust.
- **Disclaimer link:** Points to the GitHub-rendered doc
  `https://github.com/F3-Nation/password-vault/blob/main/docs/Disclaimer.md`.
  Update both templates if the disclaimer moves to another URL.
- **Logo:** This version uses a plain text "F3 NATION" wordmark (no image). Email
  clients don't render repo files or base64 reliably, so adding a real logo image
  requires a publicly reachable URL. Because the data bucket uses *uniform
  bucket-level access*, you can't make a single object public there without
  exposing the whole vault — host any logo image in a **separate public bucket**
  (or another public host) and swap the `<span>` wordmark for an `<img src="…">`.
