# Read with `tofu output` (R2 state; deploy/README.md "Cloudflare (tofu)"). The tokens go into the store yyc and from there into
# the SealedSecrets (infra/secrets.txt); the audiences into the apps' token checks.
output "tunnel_token_stream" {
  value     = data.cloudflare_zero_trust_tunnel_cloudflared_token.stream.token
  sensitive = true
}
output "tunnel_token_mailmerge" {
  value     = data.cloudflare_zero_trust_tunnel_cloudflared_token.mailmerge.token
  sensitive = true
}
output "tunnel_token_aiwatch" {
  value     = data.cloudflare_zero_trust_tunnel_cloudflared_token.aiwatch.token
  sensitive = true
}
output "mcp_oidc_client_id" {
  value = cloudflare_zero_trust_access_application.mcp_signin.saas_app.client_id
}
output "mcp_oidc_client_secret" {
  value     = cloudflare_zero_trust_access_application.mcp_signin.saas_app.client_secret
  sensitive = true
}
output "hyperdrive_client_id" {
  value = cloudflare_zero_trust_access_service_token.hyperdrive.client_id
}
output "hyperdrive_client_secret" {
  value     = cloudflare_zero_trust_access_service_token.hyperdrive.client_secret
  sensitive = true
}
output "aud" {
  value = {
    app       = cloudflare_zero_trust_access_application.app.aud
    wisers    = cloudflare_zero_trust_access_application.wisers.aud
    beta      = cloudflare_zero_trust_access_application.beta.aud
    mailmerge = cloudflare_zero_trust_access_application.mailmerge.aud
    staff_ai  = cloudflare_zero_trust_access_application.staff_ai.aud
    aiwatch   = cloudflare_zero_trust_access_application.aiwatch.aud
    nametags  = cloudflare_zero_trust_access_application.nametags.aud
    labels    = cloudflare_zero_trust_access_application.labels.aud
  }
}
output "custom_hostnames" {
  value = { for h, r in cloudflare_custom_hostname.h : h => { id = r.id, status = r.status, ownership = r.ownership_verification } }
}
