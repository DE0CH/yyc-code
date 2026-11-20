# YYC AI's Android app at ai.yyc-internal.com/apps/android.apk (apps/README.md): the Worker yyc-ai-apps (apps/worker,
# deployed by .github/workflows/yyc-ai-apps.yml) serves it from the R2 bucket yyc-ai-apps, which DE0CH/claudecodeui's
# yyc-android workflow fills after every green build on main. Its own Access app with every staff member (the users
# policy): the path is not one of pigeon's (staff_ai_paths in pigeon.tf, which pigeon's test pins), so YYC AI's own
# app stays as it is. The bucket was made once with the tofu-state token (S3 CreateBucket, 2026-11-20): this file's
# token has no R2 permission.
resource "cloudflare_zero_trust_access_application" "apps" {
  account_id                 = local.account
  name                       = "YYC AI apps"
  domain                     = "ai.${local.domain}/apps"
  destinations               = [{ type = "public", uri = "ai.${local.domain}/apps" }]
  type                       = local.app_common.type
  session_duration           = local.app_common.session_duration
  http_only_cookie_attribute = local.app_common.http_only_cookie_attribute
  auto_redirect_to_identity  = local.app_common.auto_redirect_to_identity
  app_launcher_visible       = false
  allowed_idps               = local.idps
  policies                   = [{ id = cloudflare_zero_trust_access_policy.users.id, precedence = 1 }]
}
