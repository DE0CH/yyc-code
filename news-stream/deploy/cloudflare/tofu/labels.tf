# YYC labels (labels/README.md): Worker yyc-labels (labels/worker, deployed by .github/workflows/labels-worker.yml) on
# the whole of labels.yyc-internal.com, behind the Access app below: staff sign in as on every YYC page; YYC AI's
# egress reaches the REST API with the service token yyc-labels-ai (store yyc YYC_LABELS_AI_CLIENT_ID/_SECRET, sealed
# into YYC AI's egress-env). The Worker answers every request itself, so the DNS record only has to exist and be
# proxied (100:: is Cloudflare's "no origin").
resource "cloudflare_dns_record" "labels" {
  zone_id = local.zone
  name    = "labels.${local.domain}"
  type    = "AAAA"
  content = "100::"
  proxied = true
  ttl     = 1
  comment = "YYC labels: Worker yyc-labels (behind Access app YYC 標籤)"
}
resource "cloudflare_workers_route" "labels" {
  zone_id = local.zone
  pattern = "labels.${local.domain}/*"
  script  = "yyc-labels"
}

resource "cloudflare_zero_trust_access_service_token" "labels_ai" {
  account_id = local.account
  name       = "yyc-labels-ai"
  duration   = "forever"
}
resource "cloudflare_zero_trust_access_policy" "labels_ai" {
  account_id = local.account
  name       = "YYC labels: YYC AI"
  decision   = "non_identity"
  include    = [{ service_token = { token_id = cloudflare_zero_trust_access_service_token.labels_ai.id } }]
}
resource "cloudflare_zero_trust_access_application" "labels" {
  account_id                 = local.account
  name                       = "YYC 標籤"
  domain                     = "labels.${local.domain}"
  destinations               = [{ type = "public", uri = "labels.${local.domain}" }]
  type                       = local.app_common.type
  session_duration           = local.app_common.session_duration
  http_only_cookie_attribute = local.app_common.http_only_cookie_attribute
  auto_redirect_to_identity  = local.app_common.auto_redirect_to_identity
  app_launcher_visible       = false
  allowed_idps               = local.idps
  policies = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 },
  { id = cloudflare_zero_trust_access_policy.labels_ai.id, precedence = 2 }]
}

output "labels_ai_client_id" {
  value = cloudflare_zero_trust_access_service_token.labels_ai.client_id
}
output "labels_ai_client_secret" {
  value     = cloudflare_zero_trust_access_service_token.labels_ai.client_secret
  sensitive = true
}
