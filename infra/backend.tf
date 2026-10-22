# Partial backend configuration: the (already existing) state bucket is passed
# at init time so this file never carries an environment-specific value:
#
#   cd infra
#   tofu init -backend-config="bucket=YOUR_TF_STATE_BUCKET"
#
# A fresh clone needs the bucket again (or a -backend-config=<file>.tfbackend),
# but once .terraform/ exists plain `tofu init` reuses the stored config.
terraform {
  backend "gcs" {}
}
