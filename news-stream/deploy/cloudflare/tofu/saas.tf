# Cloudflare for SaaS on yyc-internal.com: Deyao's names (deyaochen.com, his own account) are custom hostnames of this
# zone. On his zone each is a DNS-only CNAME to customers.yyc-internal.com, set by hand outside git (deploy/README.md
# "Cloudflare (tofu)"; the monitor's Cloudflare panel checks them); from there on everything (certificate, Access,
# Worker, tunnel) is on YYC's account. Free: 100 custom hostnames included, USD 0.10 each beyond.
# New YYC addresses go on yyc-internal.com itself, here (Deyao, 2026-10-23), not as more deyaochen.com names.

resource "cloudflare_custom_hostname_fallback_origin" "yyc" {
  zone_id = local.zone
  origin  = cloudflare_dns_record.origin_stream.name
}

# the name Deyao's records point at
resource "cloudflare_dns_record" "customers" {
  zone_id = local.zone
  name    = "customers.${local.domain}"
  type    = "CNAME"
  content = cloudflare_dns_record.origin_stream.name
  proxied = true
  ttl     = 1
  comment = "CNAME target of the custom hostnames (deyaochen.com records point here, DNS only)"
}

locals {
  # hostname => custom origin ("" = the fallback origin, tunnel yyc-stream)
  test_hostnames = {} # a throwaway name for trying a flip (saas-test.deyaochen.com, 2026-10-23), e.g. { "x.deyaochen.com" = "" }
  hostnames = merge(local.test_hostnames, {
    "yyc.deyaochen.com"       = "" # moved to newswatch.yyc-internal.com: Worker yyc-edge answers 301 (edge_routes)
    "yyc-beta.deyaochen.com"  = ""
    "mailmerge.deyaochen.com" = cloudflare_dns_record.origin_mailmerge.name
    "ai.yyc.deyaochen.com"    = cloudflare_dns_record.origin_ai.name # moved: Worker yyc-edge answers 301 (edge_routes)
  })
}

resource "cloudflare_custom_hostname" "h" {
  for_each             = local.hostnames
  zone_id              = local.zone
  hostname             = each.key
  custom_origin_server = each.value == "" ? null : each.value
  # http: Cloudflare renews the certificate by itself while the name CNAMEs here (the first ones were pre-issued by TXT
  # records before the 2026-10-23 flip: a name still on Cloudflare can't use http)
  ssl = {
    method   = "http"
    type     = "dv"
    wildcard = false
    settings = { min_tls_version = "1.2" }
  }
  # Cloudflare picks the certificate authority itself (google, ssl_com) and the provider reads it back as computed: without
  # this every run planned an in-place update of each name and set its certificate to active_redeploying (2026-11-04).
  # A change to `ssl` above is applied by tainting the name (tofu apply -replace) or removing this line for that run.
  lifecycle {
    ignore_changes = [ssl]
  }
  depends_on = [cloudflare_custom_hostname_fallback_origin.yyc]
}

# Access apps on these names (access.tf) need the custom hostname first (else "domain does not belong to zone").

# a throwaway test hostname gets an Access app on /private (the 2026-10-23 test: Access, Worker, tunnel, certificate)
resource "cloudflare_zero_trust_access_application" "test" {
  for_each                   = local.test_hostnames
  account_id                 = local.account
  name                       = "SaaS test ${each.key}"
  domain                     = "${each.key}/private"
  destinations               = [for u in ["${each.key}/private"] : { type = "public", uri = u }]
  type                       = "self_hosted"
  session_duration           = "24h"
  http_only_cookie_attribute = true
  app_launcher_visible       = false
  allowed_idps               = local.idps
  policies                   = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 }]
}

# the router Worker yyc-edge (news-stream/edge, deployed on this account with `deploy.sh`) in front of the
# hostnames: a route on the SaaS zone matches the custom hostname's traffic. ai.yyc.deyaochen.com: YYC AI moved to
# ai.yyc-internal.com (Deyao, 2026-10-27), and yyc.deyaochen.com, the monitoring site, to newswatch.yyc-internal.com
# (Deyao, 2026-11-03); the Worker answers 301 to the same path there.
locals {
  edge_routes = ["newswatch.${local.domain}/*", "yyc.deyaochen.com/*", "yyc-beta.deyaochen.com/*", "ai.yyc.deyaochen.com/*"]
}
resource "cloudflare_workers_route" "edge" {
  for_each = toset(local.edge_routes)
  zone_id  = local.zone
  pattern  = each.value
  script   = "yyc-edge"
}
