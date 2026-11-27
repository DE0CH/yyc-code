# The deploy workflows' path to the k8s API (deploy-workflows.md 3): tunnel yyc-deploy, whose only ingress is the API
# server as TCP (k8s-deploy.yyc-internal.com -> kubernetes.default.svc:443), behind an Access app that admits one
# service token. GitHub's runner holds only that token (environment yyc-ai): it opens the TCP path and grants nothing
# inside, where a GitHub OIDC token authenticates (clusters/talos/patches/controlplane.yaml). TLS is end to end, runner to API server.
# Its own tunnel: a mistake in an app's tunnel config cannot open the API; the connector token sits only in namespace
# yyc-deploy (clusters/talos/deploy-access.yaml, on the Talos cluster only). Outputs -> store yyc -> GitHub env secrets / SealedSecret
# yyc-deploy/tunnel (deploy-workflows 7).
#
# On since WP-59 (deploy-workflows 12.5 step 2, 2026-11-05); the connector runs on yyc-talos only.
locals {
  deploy_path = true
  deploy_n    = local.deploy_path ? 1 : 0
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "yyc_deploy" {
  count      = local.deploy_n
  account_id = local.account
  name       = "yyc-deploy"
  config_src = "cloudflare"
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "yyc_deploy" {
  count      = local.deploy_n
  account_id = local.account
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.yyc_deploy[0].id
  config = {
    ingress = [
      { hostname = "k8s-deploy.${local.domain}", service = "tcp://kubernetes.default.svc.cluster.local:443" },
      # the operator's jump host: yyc-nat2's SSH at its alias 10.0.0.30 on network yyc (from it, every node's Talos API
      # and SSH over the private network; the NAT box replaced yyc-master1 as the jump host when the old cluster went, T6)
      { hostname = "jump.${local.domain}", service = "tcp://10.0.0.30:22" },
      { service = "http_status:404" },
    ]
  }
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "yyc_deploy" {
  count      = local.deploy_n
  account_id = local.account
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.yyc_deploy[0].id
}

resource "cloudflare_dns_record" "k8s_deploy" {
  count   = local.deploy_n
  zone_id = local.zone
  name    = "k8s-deploy.${local.domain}"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.yyc_deploy[0].id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "yyc-talos k8s API for deploy workflows (tunnel yyc-deploy, Access service token only)"
}

# forever: it only opens the TCP path (authentication inside is the OIDC token), so it rotates on demand (taint it)
resource "cloudflare_zero_trust_access_service_token" "yyc_deploy_gha" {
  count      = local.deploy_n
  account_id = local.account
  name       = "yyc-deploy (GitHub Actions)"
  duration   = "forever"
}

resource "cloudflare_zero_trust_access_policy" "yyc_deploy_gha" {
  count      = local.deploy_n
  account_id = local.account
  name       = "YYC deploy API clients"
  decision   = "non_identity"
  include    = [{ service_token = { token_id = cloudflare_zero_trust_access_service_token.yyc_deploy_gha[0].id } }]
}

resource "cloudflare_zero_trust_access_application" "yyc_deploy" {
  count                = local.deploy_n
  account_id           = local.account
  name                 = "YYC deploy API"
  domain               = "k8s-deploy.${local.domain}"
  destinations         = [{ type = "public", uri = "k8s-deploy.${local.domain}" }]
  type                 = "self_hosted"
  session_duration     = "1h"
  app_launcher_visible = false
  policies             = [{ id = cloudflare_zero_trust_access_policy.yyc_deploy_gha[0].id, precedence = 1 }]
}

resource "cloudflare_dns_record" "jump" {
  count   = local.deploy_n
  zone_id = local.zone
  name    = "jump.${local.domain}"
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.yyc_deploy[0].id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "yyc-nat2's SSH, the operator's jump host to yyc-talos (tunnel yyc-deploy, Access service token only)"
}

resource "cloudflare_zero_trust_access_application" "jump" {
  count                = local.deploy_n
  account_id           = local.account
  name                 = "YYC jump host"
  domain               = "jump.${local.domain}"
  destinations         = [{ type = "public", uri = "jump.${local.domain}" }]
  type                 = "self_hosted"
  session_duration     = "1h"
  app_launcher_visible = false
  policies             = [{ id = cloudflare_zero_trust_access_policy.yyc_deploy_gha[0].id, precedence = 1 }]
}

output "yyc_deploy_tunnel_token" {
  value     = one(data.cloudflare_zero_trust_tunnel_cloudflared_token.yyc_deploy[*].token)
  sensitive = true
}
output "yyc_deploy_access_client_id" {
  value     = one(cloudflare_zero_trust_access_service_token.yyc_deploy_gha[*].client_id)
  sensitive = true
}
output "yyc_deploy_access_client_secret" {
  value     = one(cloudflare_zero_trust_access_service_token.yyc_deploy_gha[*].client_secret)
  sensitive = true
}
