# Cloudflare Access on YYC's account: the organisation (team yyc-hk), the sign-in methods, and the five apps that were on
# Deyao's account until 2026-10 (copied exactly: 720 h sessions, Google + Microsoft + an e-mailed code (off 2026-11-03, back 2026-11-05), http-only cookie; only Mail
# Merge shows in the App Launcher). Each app's allow list is a reusable policy here; changing who may sign in = a commit.

import {
  to = cloudflare_zero_trust_organization.yyc
  id = local.account
}

resource "cloudflare_zero_trust_organization" "yyc" {
  account_id                  = local.account
  name                        = "YYC"
  auth_domain                 = "${local.team}.cloudflareaccess.com"
  is_ui_read_only             = false
  strict_service_token_auth   = true # de0ch has false; accounts made after 2026-10-05 can't turn it off (API error 12062)
  allow_authenticate_via_warp = false
  auto_redirect_to_identity   = false
  deny_unmatched_requests     = false
  # Cloudflare's own sign-in page, made to look like YYC AI's web app (Deyao, 2026-10-28; DE0CH/claudecodeui paseo,
  # packages/app). Cloudflare takes only these five fields, so: the page is the app's light grey (surfaceSidebar,
  # styles/theme.ts) so Cloudflare's white card sits on it as the app's white chat pane sits beside its sidebar; the
  # text is the app's foreground (theme.ts); the logo is the app's own (public/yyc-logo.png, its navy tile carries
  # YYC's teal, the app's accent), from YYC AI's public shell, served without sign-in (jarvis/router.mjs "Signed out");
  # no header text: Cloudflare draws it in a grey box above the sign-in buttons that reads as a button (Deyao,
  # 2026-10-28); the footer says who may sign in, in the app's voice.
  login_design = {
    background_color = "#F4F4F5"
    text_color       = "#1A1A1E"
    logo_path        = "https://ai.${local.domain}/yyc-logo.png"
    footer_text      = "只限獲授權的 YYC 同事使用。"
  }
}

# The e-mailed one-time code (Deyao, 2026-11-05: back beside Google and Microsoft): any address in the catalogue can sign
# in with a code Cloudflare sends it, e.g. bot@deyaochen.com for browser tests. Access sends no code to an address
# outside the catalogue (it still says it did).
resource "cloudflare_zero_trust_access_identity_provider" "otp" {
  account_id = local.account
  name       = "E-mailed code"
  type       = "onetimepin"
  config     = {}
}

resource "cloudflare_zero_trust_access_identity_provider" "google" {
  count      = var.google_client_id == "" ? 0 : 1
  account_id = local.account
  name       = "Google"
  type       = "google"
  config = {
    client_id     = var.google_client_id
    client_secret = var.google_client_secret
  }
}

resource "cloudflare_zero_trust_access_identity_provider" "microsoft" {
  count      = var.entra_client_id == "" ? 0 : 1
  account_id = local.account
  name       = "YYC 公司帳戶 (Microsoft 365)" # the button reads "Azure AD ・ <name>": Cloudflare fixes the first half
  type       = "azureAD"
  config = {
    client_id     = var.entra_client_id
    client_secret = var.entra_client_secret
    directory_id  = var.entra_tenant_id
  }
}

locals {
  # the e-mailed code, Google and Microsoft on every YYC app (the code was off 2026-11-03 to 2026-11-05, Deyao)
  idps = concat([cloudflare_zero_trust_access_identity_provider.otp.id],
    [for g in cloudflare_zero_trust_access_identity_provider.google : g.id],
  [for m in cloudflare_zero_trust_access_identity_provider.microsoft : m.id])

  # who may sign in: ONE catalogue for every YYC app (Deyao, 2026-10-28), `users` (normal users: everyone) and `admins`
  # (only Deyao) of var.catalogue. YYC AI's hermit and tools and yyc-mcp read the same value. "@domain" = everyone there.
  yyc_users = jsondecode(var.catalogue).users
  # YYC AI's paths that need sign-in; everything else on the host is its public shell (the router serves only the built
  # web app there, Deyao 2026-10-27 "interface first"). Same list as the router's SIGNED_IN_PATHS (DE0CH/claudecodeui
  # jarvis/router.mjs) and the tunnel's access-required rule (tunnels.tf). The lists: staff_ai_hosts, staff_ai_paths
  # in pigeon.tf.

  app_common = {
    type                       = "self_hosted"
    session_duration           = "720h"
    http_only_cookie_attribute = true
    auto_redirect_to_identity  = false
  }
}

# One kind of access for every YYC app (Deyao, 2026-11-03): the catalogue's `users` as one reusable policy, which every
# staff app below takes (`admins` in the catalogue is YYC AI's own operator list, checked by its router, not Access).
# Moved from the per-app list staff_ai_users, so the policy is never recreated: an update in place, never a gap.
moved {
  from = cloudflare_zero_trust_access_policy.staff_ai_users
  to   = cloudflare_zero_trust_access_policy.users
}
resource "cloudflare_zero_trust_access_policy" "users" {
  account_id = local.account
  name       = "YYC users"
  decision   = "allow"
  include = concat(
    [for e in local.yyc_users : { email = { email = e } } if !startswith(e, "@")],
    [for e in local.yyc_users : { email_domain = { domain = trimprefix(e, "@") } } if startswith(e, "@")],
  )
}

resource "cloudflare_zero_trust_access_application" "app" {
  account_id                 = local.account
  name                       = "YYC 新聞監察"
  domain                     = "newswatch.${local.domain}/app"
  destinations               = [for u in ["newswatch.${local.domain}/app"] : { type = "public", uri = u }]
  type                       = local.app_common.type
  session_duration           = local.app_common.session_duration
  http_only_cookie_attribute = local.app_common.http_only_cookie_attribute
  auto_redirect_to_identity  = local.app_common.auto_redirect_to_identity
  app_launcher_visible       = false
  allowed_idps               = local.idps
  policies                   = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 }]
  depends_on                 = [cloudflare_custom_hostname.h]
}

resource "cloudflare_zero_trust_access_application" "wisers" {
  account_id                 = local.account
  name                       = "YYC WiseSearch"
  domain                     = "newswatch.${local.domain}/app/wisers"
  destinations               = [for u in ["newswatch.${local.domain}/app/wisers"] : { type = "public", uri = u }]
  type                       = local.app_common.type
  session_duration           = local.app_common.session_duration
  http_only_cookie_attribute = local.app_common.http_only_cookie_attribute
  auto_redirect_to_identity  = local.app_common.auto_redirect_to_identity
  app_launcher_visible       = false
  allowed_idps               = local.idps
  policies                   = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 }]
  depends_on                 = [cloudflare_custom_hostname.h]
}

resource "cloudflare_zero_trust_access_application" "beta" {
  account_id                 = local.account
  name                       = "YYC 新聞監察 (beta)"
  domain                     = "yyc-beta.deyaochen.com"
  destinations               = [for u in ["yyc-beta.deyaochen.com"] : { type = "public", uri = u }]
  type                       = local.app_common.type
  session_duration           = local.app_common.session_duration
  http_only_cookie_attribute = local.app_common.http_only_cookie_attribute
  auto_redirect_to_identity  = local.app_common.auto_redirect_to_identity
  app_launcher_visible       = false
  allowed_idps               = local.idps
  policies                   = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 }]
  depends_on                 = [cloudflare_custom_hostname.h]
}

resource "cloudflare_zero_trust_access_application" "mailmerge" {
  account_id                 = local.account
  name                       = "YYC Mail Merge"
  domain                     = "mailmerge.deyaochen.com"
  destinations               = [for u in ["mailmerge.deyaochen.com"] : { type = "public", uri = u }]
  type                       = local.app_common.type
  session_duration           = local.app_common.session_duration
  http_only_cookie_attribute = local.app_common.http_only_cookie_attribute
  auto_redirect_to_identity  = local.app_common.auto_redirect_to_identity
  app_launcher_visible       = true
  allowed_idps               = local.idps
  policies                   = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 }]
  depends_on                 = [cloudflare_custom_hostname.h]
}

resource "cloudflare_zero_trust_access_application" "staff_ai" {
  account_id                 = local.account
  name                       = "YYC AI"
  domain                     = "ai.${local.domain}/api"
  destinations               = flatten([for h in local.staff_ai_hosts : [for p in local.staff_ai_paths : { type = "public", uri = "${h}/${p}" }]])
  type                       = local.app_common.type
  session_duration           = local.app_common.session_duration
  http_only_cookie_attribute = local.app_common.http_only_cookie_attribute
  auto_redirect_to_identity  = local.app_common.auto_redirect_to_identity
  app_launcher_visible       = false
  allowed_idps               = local.idps
  policies                   = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 }]
}

# yyc-mcp's sign-in step (mcp.yyc-internal.com, news-stream/mcp-edge) for its web pages (/tokens, /admin): Access as its
# OpenID Connect provider, so the login and the "YYC users" list are every other YYC app's. Its REST API stays outside
# Access (scripts and YYC AI send a bearer token); the server checks the catalogue again on every call.
# Client id and secret: outputs mcp_oidc_* -> store yyc YYC_MCP_OIDC_CLIENT_* -> Secret yyc/mcp-env.
resource "cloudflare_zero_trust_access_application" "mcp_signin" {
  account_id           = local.account
  name                 = "YYC MCP sign-in"
  type                 = "saas"
  session_duration     = local.app_common.session_duration
  app_launcher_visible = false
  allowed_idps         = local.idps
  policies             = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 }]
  saas_app = {
    auth_type     = "oidc"
    redirect_uris = ["https://mcp.${local.domain}/callback"]
    grant_types   = ["authorization_code_with_pkce"]
    scopes        = ["openid", "email", "profile"]
  }
}

# YYC AI watch (ai-watch/): on yyc-internal.com itself, so no custom hostname
resource "cloudflare_zero_trust_access_application" "aiwatch" {
  account_id                 = local.account
  name                       = "YYC AI 觀察"
  domain                     = "aiwatch.${local.domain}"
  destinations               = [{ type = "public", uri = "aiwatch.${local.domain}" }]
  type                       = local.app_common.type
  session_duration           = local.app_common.session_duration
  http_only_cookie_attribute = local.app_common.http_only_cookie_attribute
  auto_redirect_to_identity  = local.app_common.auto_redirect_to_identity
  app_launcher_visible       = false
  allowed_idps               = local.idps
  policies                   = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 }]
}
