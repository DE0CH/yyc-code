# YYC's Cloudflare account (YYC, 00916611232966eaf40906a57d09394e) as code: OpenTofu, applied by the GitHub workflow
# .github/workflows/cloudflare.yml (README "Cloudflare (tofu)"). Git is the truth: every change here is planned and
# applied on push, and every 30 minutes a run reverts what was changed in the dashboard. State: R2 (below). Outputs
# (tunnel tokens, the service token, the Access audiences): `tofu output` with the R2 key.
#
# What the dashboard did once and code can't (documented, not scripted): the domain yyc-internal.com (moved from
# Deyao's account 2026-10-23, Registrar on this account), its zone (Free), Zero Trust Free and Cloudflare for SaaS
# (both USD 0, card on file).
terraform {
  required_version = ">= 1.10"
  required_providers {
    cloudflare = { source = "cloudflare/cloudflare", version = "5.28.1" }
  }
  # State in R2 on YYC's own account (bucket yyc-tofu-state; token yyc-tofu-state, R2 read+write on this account only:
  # YYC_TOFU_R2_ACCESS_KEY_ID / _SECRET_ACCESS_KEY). The lock is a lockfile beside it (S3 conditional writes).
  backend "s3" {
    bucket                      = "yyc-tofu-state"
    key                         = "cloudflare/terraform.tfstate"
    region                      = "auto"
    endpoints                   = { s3 = "https://00916611232966eaf40906a57d09394e.r2.cloudflarestorage.com" }
    use_lockfile                = true
    use_path_style              = true
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_s3_checksum            = true
  }
}

variable "cloudflare_api_token" {
  type      = string
  sensitive = true # Actions secret YYC_CF_ACCESS_EDGE_TOKEN (token yyc-access-edge, YYC account only)
}

# The Google sign-in client of YYC's Access (its own Google Cloud project, redirect https://yyc-hk.cloudflareaccess.com/cdn-cgi/access/callback).
# Empty = no Google button (the Microsoft one is then the only sign-in method).
variable "cloudflare_turnstile_token" {
  type      = string
  sensitive = true # Actions secret YYC_CF_TURNSTILE_TOKEN (token yyc-turnstile: Turnstile Sites only, YYC account)
}
variable "google_client_id" {
  type    = string
  default = ""
}
variable "google_client_secret" {
  type      = string
  default   = ""
  sensitive = true
}
# The Microsoft sign-in: Entra app "Cloudflare Access (YYC staff sign-in)" in YYC's Microsoft 365 tenant (YUAN YI
# CONSULTING LIMITED), redirect https://yyc-hk.cloudflareaccess.com/cdn-cgi/access/callback, admin-consented for the
# organisation; secret expires 2028-11-01. Actions secrets YYC_ENTRA_* (store yyc). Empty = no Microsoft button.
variable "entra_client_id" {
  type    = string
  default = ""
}
variable "entra_client_secret" {
  type      = string
  default   = ""
  sensitive = true
}
variable "entra_tenant_id" {
  type    = string
  default = ""
}
# YYC's access catalogue, {"users": [...], "admins": [...]}: people's mail addresses, so never a file in git. The
# store's YYC_CATALOGUE_JSON (store `yyc`), the same value the clusters' Secrets are sealed from (infra/secrets.txt).
variable "catalogue" {
  type      = string
  sensitive = true # Actions secret YYC_CATALOGUE_JSON
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
# Turnstile widgets only (yyc-access-edge has no Turnstile permission)
provider "cloudflare" {
  alias     = "turnstile"
  api_token = var.cloudflare_turnstile_token
}

locals {
  account = "00916611232966eaf40906a57d09394e"
  zone    = "20fd1e5a919a87b5d54c1e037b292ee7" # yyc-internal.com
  domain  = "yyc-internal.com"
  team    = "yyc-hk" # yyc-hk.cloudflareaccess.com (yyc.cloudflareaccess.com belongs to someone else)
}
