// Cloudflare Access (YYC's team yyc-hk, on YYC's own Cloudflare account): the approved accounts are the Access policy of newswatch.yyc-internal.com/app (and
// everyone signed in on the beta site, yyc-beta.deyaochen.com, whose whole host is behind its own Access app).
// Cloudflare sends the signed token (Cf-Access-Jwt-Assertion, RS256) only on those paths; it is checked here again,
// signature, audience and expiry, before anything for approved accounts is answered.
import { createLocalJWKSet, createRemoteJWKSet, jwtVerify, type JSONWebKeySet } from "jose";

const TEAM = "https://yyc-hk.cloudflareaccess.com";
export const APP_AUD = "8b644dde1fadd76a241cace4428d57ebe8fccb6cdfe0af987e5f35bc7e31dd9b";   // newswatch.yyc-internal.com/app
export const BETA_AUD = "20d2253da8891160d5933c4036edf043d94e69bfc65c0839b86bba060bae9609";  // yyc-beta.deyaochen.com
export const WISERS_AUD = "af0134321afca2f355d7782cb0ad2a2c06e8f901208d8e8d3bd121941d3e5cab";  // newswatch.yyc-internal.com/app/wisers
export { ADMIN_EMAIL } from "../lib/admin";
export const NOT_APPROVED = "只供 Deyao 批准的帳戶使用";

const jwks = createRemoteJWKSet(new URL(TEAM + "/cdn-cgi/access/certs"));

// Tests only: the vite dev server (import.meta.env.DEV), or a local `vite preview` of a build (a request to 127.0.0.1 or
// localhost), verifies tokens against a local public key when the env has ACCESS_TEST_JWKS (a JWKS JSON), so Playwright
// can sign in as anyone with tokens it signs itself. On Cloudflare a request's host is never 127.0.0.1 or localhost
// and import.meta.env.DEV is false, so there only Cloudflare's keys count.
let testKeys: ReturnType<typeof createLocalJWKSet> | null | undefined;
async function keySet(req: Request) {
  if (import.meta.env.DEV || /^(127\.0\.0\.1|localhost)$/.test(new URL(req.url).hostname)) {
    if (testKeys === undefined) {
      const { env } = await import("cloudflare:workers");
      const local = (env as unknown as { ACCESS_TEST_JWKS?: string }).ACCESS_TEST_JWKS;
      testKeys = local ? createLocalJWKSet(JSON.parse(local) as JSONWebKeySet) : null;
    }
    if (testKeys) return testKeys;
  }
  return jwks;
}

/** the approved account's email (lower case), or null; `audience`: the Access app(s) the token must be for (the
 *  WiseSearch page has its own app, WISERS_AUD, whose policy is the WiseSearch accounts only) */
export async function accessUser(req: Request, audience: string[] = [APP_AUD, BETA_AUD]): Promise<string | null> {
  const tok = req.headers.get("Cf-Access-Jwt-Assertion");
  if (!tok) return null;
  try {
    const { payload } = await jwtVerify(tok, await keySet(req), { issuer: TEAM, audience, algorithms: ["RS256"] });
    const email = typeof payload.email === "string" ? payload.email.toLowerCase() : "";
    return email || null;
  } catch {
    return null;
  }
}

export const json = (status: number, body: unknown, extra: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "private, no-store", ...extra } });

/** a handler for approved accounts only: 403 with the note for everyone else */
export function approved(fn: (req: Request, email: string, url: URL) => Promise<Response>) {
  return async ({ request }: { request: Request }) => {
    const email = await accessUser(request);
    if (!email) return json(403, { error: NOT_APPROVED });
    return fn(request, email, new URL(request.url));
  };
}
