// /app/health/app-signin?state=…: the "YYC Systems" iPhone app's sign-in (DE0CH/yyc-health-ios). The app opens this in
// the system sign-in sheet (ASWebAuthenticationSession); Cloudflare Access signs Deyao in there (Google), then this
// sends the sheet to the app's scheme with his Access token, which the app keeps in its Keychain and gives its WebView
// as the CF_Authorization cookie (the same token as the Cf-Access-Jwt-Assertion header). As YYC AI's app sign-in
// (pigeon /api/jarvis/app-signin). Deyao's identity only (gates.ts and here); `state` is the app's own random value,
// returned as it came so the app knows the answer is to its request.
import { createFileRoute } from "@tanstack/react-router";
import { accessUser, ADMIN_EMAIL } from "../server/access";

export const APP_SCHEME = "dev.de0ch.yycsystems";
const head = { "Cache-Control": "no-store", "X-Robots-Tag": "noindex" };

export const Route = createFileRoute("/app/health_/app-signin")({
  server: { handlers: { GET: async ({ request }) => {
    if ((await accessUser(request)) !== ADMIN_EMAIL) return new Response("not found", { status: 404, headers: { "Content-Type": "text/plain", ...head } });
    const state = new URL(request.url).searchParams.get("state") || "";
    if (!/^[A-Za-z0-9_-]{16,128}$/.test(state)) return new Response("bad state", { status: 400, headers: { "Content-Type": "text/plain", ...head } });
    const token = request.headers.get("Cf-Access-Jwt-Assertion") || "";
    return new Response(null, { status: 302, headers: { Location: `${APP_SCHEME}://signed-in?state=${state}#token=${encodeURIComponent(token)}`, ...head } });
  } } },
});
