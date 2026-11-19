# YYC AI's new stack (DE0CH/aviary yyc/refactor spec-platform 14.1, DESIGN 15.10): tunnel yyc-ai-pigeon (connectors
# yyc-ai/cloudflared on the new Talos cluster) and the host and path lists both YYC AI tunnels (tunnels.tf) and the
# Access app (access.tf) use. The old stack's tunnel yyc-ai went in A6 (2026-11-07).
resource "cloudflare_zero_trust_tunnel_cloudflared" "pigeon" {
  account_id = local.account
  name       = "yyc-ai-pigeon"
  config_src = "cloudflare"
}

# YYC AI's hosts and paths with the staff Access rule, to pigeon; origin_ai (tunnels.tf) resolves here
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "pigeon" {
  account_id = local.account
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.pigeon.id
  config = {
    ingress = concat(flatten([for h in local.staff_ai_hosts : [
      {
        hostname       = h
        path           = "^/(${join("|", local.staff_ai_paths)})(/.*)?$"
        service        = "http://pigeon.yyc-ai.svc.cluster.local:80"
        origin_request = { access = { required = true, team_name = local.team, aud_tag = [cloudflare_zero_trust_access_application.staff_ai.aud] } }
      },
      { hostname = h, service = "http://pigeon.yyc-ai.svc.cluster.local:80" },
      ]]), [
      # YYC Drive (drive.tf): the whole host behind its Access app
      {
        hostname       = local.drive_host
        service        = "http://drive.yyc-ai.svc.cluster.local:80"
        origin_request = { access = { required = true, team_name = local.team, aud_tag = [cloudflare_zero_trust_access_application.drive.aud] } }
      },
    ], [{ service = "http_status:404" }])
  }
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "pigeon" {
  account_id = local.account
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.pigeon.id
}

# the connectors' token: store yyc YYC_CF_PIGEON_TUNNEL_TOKEN, sealed into yyc-ai/tunnel
output "tunnel_token_pigeon" {
  value     = data.cloudflare_zero_trust_tunnel_cloudflared_token.pigeon.token
  sensitive = true
}

locals {
  # the hosts tunnel pigeon answers (origin_ai resolves to it)
  staff_ai_hosts = ["ai.${local.domain}"]
  # pigeon's Access-gated prefixes (aviary pigeon 17.5's test requires exactly these)
  staff_ai_paths = ["api", "paseo-ws", "ops"]
}
