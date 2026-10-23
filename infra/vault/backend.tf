# Partial backend configuration, same pattern as the sync-job root: the state
# bucket is passed at init time so this file never carries an environment value:
#
#   tofu -chdir=infra/vault init -backend-config="bucket=YOUR_TF_STATE_BUCKET"
#
# The explicit `prefix` is what keeps this root's state (infra-vault/default.tfstate)
# from colliding with the sync-job root's bare default.tfstate in the same bucket.
terraform {
  backend "gcs" {
    prefix = "infra-vault"
  }
}
