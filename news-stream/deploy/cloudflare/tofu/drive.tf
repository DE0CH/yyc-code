# YYC Drive (DE0CH/aviary yyc/deploy/drive/drive.yaml; DE0CH/yyc-drive): one shared folder for the whole company at
# drive.yyc-internal.com. Every path needs sign-in: the Access app covers the host. The Worker yyc-drive (deployed by
# yyc-ai.yml job drive-edge) answers the whole host at the edge: the page, its settings and sign-in, for the person in
# Access's token; it passes the folder's calls (/dav/, /data/) to the origin below, the tunnel yyc-ai-pigeon, whose
# rule (pigeon.tf) has cloudflared check Access's token again before a request reaches the pods, which trust the header
# naming the person (the Worker sets it from the token).
locals {
  drive_host = "drive.${local.domain}"
}

resource "cloudflare_zero_trust_access_application" "drive" {
  account_id                 = local.account
  name                       = "YYC Drive"
  domain                     = local.drive_host
  type                       = local.app_common.type
  session_duration           = local.app_common.session_duration
  http_only_cookie_attribute = local.app_common.http_only_cookie_attribute
  auto_redirect_to_identity  = local.app_common.auto_redirect_to_identity
  app_launcher_visible       = true
  allowed_idps               = local.idps
  policies                   = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 }]
}

resource "cloudflare_dns_record" "drive" {
  zone_id = local.zone
  name    = local.drive_host
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.pigeon.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "YYC Drive (Access app YYC Drive): tunnel pigeon"
}

resource "cloudflare_workers_route" "drive" {
  zone_id = local.zone
  pattern = "${local.drive_host}/*"
  script  = "yyc-drive"
}
