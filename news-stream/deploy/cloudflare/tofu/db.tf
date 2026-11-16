# The edge site's path to YYC's Postgres (Deyao, 2026-10-27: self-hosted, not Neon): Cloudflare
# Hyperdrive config yyc-pg -> db.yyc-internal.com -> tunnel yyc-stream -> Service pg-shared.db (YYC's Postgres, on either cluster).
# Hyperdrive reaches a database behind a tunnel through Access: the app below admits only the service token yyc-hyperdrive,
# whose id and secret the Hyperdrive config carries (set by API with the role yyc_edge's password; deploy/README.md
# "Hyperdrive"). The tunnel's ingress rule is in tunnels.tf.

resource "cloudflare_zero_trust_access_service_token" "hyperdrive" {
  account_id = local.account
  name       = "yyc-hyperdrive"
  duration   = "forever"
}

resource "cloudflare_zero_trust_access_policy" "hyperdrive" {
  account_id = local.account
  name       = "Hyperdrive yyc-pg"
  decision   = "non_identity"
  include    = [{ service_token = { token_id = cloudflare_zero_trust_access_service_token.hyperdrive.id } }]
}

resource "cloudflare_zero_trust_access_application" "db" {
  account_id           = local.account
  name                 = "YYC Postgres (Hyperdrive only)"
  domain               = "db.${local.domain}"
  destinations         = [{ type = "public", uri = "db.${local.domain}" }]
  type                 = "self_hosted"
  session_duration     = "24h"
  app_launcher_visible = false
  policies             = [{ id = cloudflare_zero_trust_access_policy.hyperdrive.id, precedence = 1 }]
}

resource "cloudflare_dns_record" "db" {
  zone_id = local.zone
  name    = "db.${local.domain}"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.stream.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "Postgres for Hyperdrive yyc-pg: tunnel yyc-stream, Access token yyc-hyperdrive"
}
