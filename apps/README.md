# YYC AI's app downloads

`https://ai.yyc-internal.com/apps/android.apk`: the newest YYC AI Android app (`dev.de0ch.yycai`), the button in YYC AI's
Settings → General → Apps, on every device (DE0CH/aviary app/docs/paseo-ui.md "The Android app").

- **Where it comes from:** DE0CH/aviary's `app-android` workflow builds and tests the signed APK on every push and,
  on main, puts it in the R2 bucket `yyc-ai-apps` (object `android.apk`; token `yyc-ai-apps-upload`, that bucket only).
- **How it is served:** the Worker `yyc-ai-apps` (`worker/`, route in `news-stream/deploy/cloudflare/tofu/apps.tf`)
  checks Access's token (the Access app "YYC AI apps", every staff member) and answers from the Cloudflare location's
  cache, keyed by the object's ETag, so the file comes from the edge and a new upload is served from the next request.
- **Deploys:** `.github/workflows/yyc-ai-apps.yml` (`npm test`, then `wrangler deploy` on main).
