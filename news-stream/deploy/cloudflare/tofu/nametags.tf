# YYC name tags (nametags/README.md): Worker yyc-nametags (nametags/worker, deployed by
# .github/workflows/nametags-worker.yml) on the whole of tags.yyc-internal.com, behind the Access app below. The Worker
# answers every request itself, so the DNS record only has to exist and be proxied (100:: is Cloudflare's "no origin").
resource "cloudflare_dns_record" "nametags" {
  zone_id = local.zone
  name    = "tags.${local.domain}"
  type    = "AAAA"
  content = "100::"
  proxied = true
  ttl     = 1
  comment = "YYC name tags: Worker yyc-nametags (behind Access app YYC 名牌報名)"
}
resource "cloudflare_workers_route" "nametags" {
  zone_id = local.zone
  pattern = "tags.${local.domain}/*"
  script  = "yyc-nametags"
}

# the Mac print bridge (claude-env scripts/niimbot/niimbot.py serve; store yyc YYC_NAMETAGS_BRIDGE_ID/_SECRET), also
# used by nametags/import.py for PUT /api/admin/guests
resource "cloudflare_zero_trust_access_service_token" "nametags_bridge" {
  account_id = local.account
  name       = "yyc-nametags-bridge"
  duration   = "forever"
}
resource "cloudflare_zero_trust_access_policy" "nametags_bridge" {
  account_id = local.account
  name       = "YYC name tags bridge"
  decision   = "non_identity"
  include    = [{ service_token = { token_id = cloudflare_zero_trust_access_service_token.nametags_bridge.id } }]
}

resource "cloudflare_zero_trust_access_application" "nametags" {
  account_id                 = local.account
  name                       = "YYC 名牌報名"
  domain                     = "tags.${local.domain}"
  destinations               = [{ type = "public", uri = "tags.${local.domain}" }]
  type                       = local.app_common.type
  session_duration           = local.app_common.session_duration
  http_only_cookie_attribute = local.app_common.http_only_cookie_attribute
  auto_redirect_to_identity  = local.app_common.auto_redirect_to_identity
  app_launcher_visible       = false
  allowed_idps               = local.idps
  policies = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 },
  { id = cloudflare_zero_trust_access_policy.nametags_bridge.id, precedence = 2 }]
}

output "nametags_bridge_client_id" {
  value = cloudflare_zero_trust_access_service_token.nametags_bridge.client_id
}
output "nametags_bridge_client_secret" {
  value     = cloudflare_zero_trust_access_service_token.nametags_bridge.client_secret
  sensitive = true
}

# The public registration form (nametags/README.md "Registration → QR → print"): the same Worker on
# register.yyc-internal.com, with NO Access app (guests have no YYC login; Deyao, 2026-10-30). The Worker serves only
# the public routes on this host and checks Turnstile on every submit.
resource "cloudflare_dns_record" "nametags_register" {
  zone_id = local.zone
  name    = "register.${local.domain}"
  type    = "AAAA"
  content = "100::"
  proxied = true
  ttl     = 1
  comment = "YYC name tags: public registration form (Worker yyc-nametags, no Access)"
}
resource "cloudflare_workers_route" "nametags_register" {
  zone_id = local.zone
  pattern = "register.${local.domain}/*"
  script  = "yyc-nametags"
}
resource "cloudflare_turnstile_widget" "nametags_register" {
  provider   = cloudflare.turnstile
  account_id = local.account
  name       = "yyc-nametags-register"
  domains    = ["register.${local.domain}"]
  mode       = "managed"
}

# sitekey: TURNSTILE_SITEKEY in nametags/worker/wrangler.jsonc; secret: the Worker secret TURNSTILE_SECRET
output "nametags_turnstile_sitekey" {
  value = cloudflare_turnstile_widget.nametags_register.sitekey
}
output "nametags_turnstile_secret" {
  value     = cloudflare_turnstile_widget.nametags_register.secret
  sensitive = true
}
