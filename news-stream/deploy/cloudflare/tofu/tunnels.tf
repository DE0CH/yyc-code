# The tunnels into the cluster, remotely managed (their ingress is here). The connectors run in the cluster
# (cloudflared in yyc, mailmerge, aiwatch; YYC AI's tunnel is in pigeon.tf) with the tokens from the outputs (store yyc -> SealedSecrets). The ingress
# hostnames are the custom hostnames (saas.tf): Cloudflare sends a custom hostname's request to its origin record
# below with Host = the custom hostname, and cloudflared matches the rule by that Host.

resource "cloudflare_zero_trust_tunnel_cloudflared" "stream" {
  account_id = local.account
  name       = "yyc-stream"
  config_src = "cloudflare"
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "mailmerge" {
  account_id = local.account
  name       = "mailmerge"
  config_src = "cloudflare"
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "aiwatch" {
  account_id = local.account
  name       = "aiwatch"
  config_src = "cloudflare"
}

# the news stream: the AI server (via the connector pod's socat on 127.0.0.1:8080 -> Service web), the WiseSearch
# relay and the MCP server (mcp.yyc-internal.com, outside Access: OAuth + the catalogue in the server); the beta site is
# the same origin; Postgres for Hyperdrive (db.tf)
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "stream" {
  account_id = local.account
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.stream.id
  config = {
    ingress = concat([
      { hostname = "newswatch.${local.domain}", path = "^/app/wisers", service = "http://wisers.yyc.svc.cluster.local:8899" },
      { hostname = "newswatch.${local.domain}", service = "http://127.0.0.1:8080" },
      { hostname = "yyc-beta.deyaochen.com", service = "http://127.0.0.1:8080" },
      { hostname = "mcp.${local.domain}", service = "http://yyc-mcp.yyc.svc.cluster.local:80" },
      { hostname = "db.${local.domain}", service = "tcp://pg-shared.db.svc.cluster.local:5432" }, # Hyperdrive yyc-pg (db.tf); pg-shared answers on both clusters (T4b)
      ], [for h in keys(local.test_hostnames) : { hostname = h, service = "hello_world" }],
    [{ service = "http_status:404" }])
  }
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "mailmerge" {
  account_id = local.account
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.mailmerge.id
  config = {
    ingress = [
      { hostname = "mailmerge.deyaochen.com", service = "http://mailmerge.mailmerge.svc.cluster.local:80" },
      { service = "http_status:404" },
    ]
  }
}

# YYC AI watch: cloudflared admits only requests carrying a valid token of its Access app
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "aiwatch" {
  account_id = local.account
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.aiwatch.id
  config = {
    ingress = [
      {
        hostname       = "aiwatch.${local.domain}"
        service        = "http://web.aiwatch.svc.cluster.local:80"
        origin_request = { access = { required = true, team_name = local.team, aud_tag = [cloudflare_zero_trust_access_application.aiwatch.aud] } }
      },
      { service = "http_status:404" },
    ]
  }
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "stream" {
  account_id = local.account
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.stream.id
}
data "cloudflare_zero_trust_tunnel_cloudflared_token" "mailmerge" {
  account_id = local.account
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.mailmerge.id
}
data "cloudflare_zero_trust_tunnel_cloudflared_token" "aiwatch" {
  account_id = local.account
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.aiwatch.id
}

# origin records on yyc-internal.com (proxied): what the custom hostnames are sent to. A request to one of these names
# itself carries Host = that name, which no ingress rule matches (404).
resource "cloudflare_dns_record" "origin_stream" {
  zone_id = local.zone
  name    = "stream.${local.domain}"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.stream.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "origin of yyc / yyc-beta (fallback origin): tunnel yyc-stream"
}
resource "cloudflare_dns_record" "newswatch" {
  zone_id = local.zone
  name    = "newswatch.${local.domain}"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.stream.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "the monitoring site (Worker yyc-edge in front, saas.tf edge_routes): tunnel yyc-stream"
}
resource "cloudflare_dns_record" "mcp" {
  zone_id = local.zone
  name    = "mcp.${local.domain}"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.stream.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "yyc-mcp (MCP + REST for AI apps): tunnel yyc-stream"
}
resource "cloudflare_dns_record" "origin_mailmerge" {
  zone_id = local.zone
  name    = "mailmerge.${local.domain}"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.mailmerge.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "origin of mailmerge.deyaochen.com: tunnel mailmerge"
}
resource "cloudflare_dns_record" "origin_ai" {
  zone_id = local.zone
  name    = "ai.${local.domain}"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.pigeon.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "YYC AI (Access app YYC AI): tunnel pigeon"
}
resource "cloudflare_dns_record" "aiwatch" {
  zone_id = local.zone
  name    = "aiwatch.${local.domain}"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.aiwatch.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "YYC AI watch: tunnel aiwatch (behind Access app YYC AI watch)"
}
# AI watch's edge (ai-watch/edge: Worker yyc-aiwatch, deployed by .github/workflows/ai-watch-edge.yml) on the whole
# hostname: the pages come from Cloudflare's assets, /api/* goes on to the tunnel above. Access checks first.
resource "cloudflare_workers_route" "aiwatch" {
  zone_id = local.zone
  pattern = "aiwatch.${local.domain}/*"
  script  = "yyc-aiwatch"
}
