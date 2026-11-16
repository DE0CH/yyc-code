// The sign-in step: Cloudflare Access as the OpenID Connect provider (Access for SaaS app "YYC MCP sign-in", policy "YYC
// users": Google or an e-mailed code, tofu access.tf), with PKCE; and the small signed cookies of the web pages
// (/tokens, /admin).
import type { Env } from "./env";

const enc = new TextEncoder();
export const b64url = (b: ArrayBuffer | Uint8Array) =>
  btoa(String.fromCharCode(...new Uint8Array(b instanceof Uint8Array ? b : new Uint8Array(b)))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const fromB64url = (s: string) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), c => c.charCodeAt(0));

export const randomToken = (n = 32) => { const b = new Uint8Array(n); crypto.getRandomValues(b); return b64url(b); };

export async function s256(verifier: string) {
  return b64url(await crypto.subtle.digest("SHA-256", enc.encode(verifier)));
}

const hmacKey = (secret: string) => crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);

async function hmac(secret: string, data: string) {
  return b64url(await crypto.subtle.sign("HMAC", await hmacKey(secret), enc.encode(data)));
}

/** a value signed with CONSENT_SECRET, expiring after `ttl` seconds */
export async function seal(env: Env, purpose: string, value: unknown, ttl: number) {
  const body = b64url(enc.encode(JSON.stringify({ v: value, exp: Date.now() + ttl * 1000, p: purpose })));
  return body + "." + await hmac(env.CONSENT_SECRET, purpose + "." + body);
}

export async function unseal<T>(env: Env, purpose: string, sealed: string | null | undefined): Promise<T | null> {
  if (!sealed) return null;
  const [body, sig] = sealed.split(".");
  if (!body || !sig) return null;
  try {
    // crypto.subtle.verify compares in constant time
    if (!await crypto.subtle.verify("HMAC", await hmacKey(env.CONSENT_SECRET), fromB64url(sig), enc.encode(purpose + "." + body))) return null;
    const o = JSON.parse(new TextDecoder().decode(fromB64url(body)));
    return o.p === purpose && o.exp > Date.now() ? (o.v as T) : null;
  } catch { return null; }
}

export function cookie(req: Request, name: string) {
  for (const part of (req.headers.get("cookie") || "").split(/;\s*/)) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i) === name) return part.slice(i + 1);
  }
  return null;
}

export const setCookie = (name: string, value: string, maxAge: number) =>
  `${name}=${value}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=${maxAge}`;

export const SESSION = "__Host-yyc-session", LOGIN = "__Host-yyc-login";

export interface User { sub: string; email: string; name: string | null }

const endpoint = (env: Env, name: "authorization" | "token" | "userinfo") => env.OIDC_ISSUER.replace(/\/$/, "") + "/" + name;

export function authorizeUrl(env: Env, state: string, challenge: string) {
  const u = new URL(endpoint(env, "authorization"));
  u.search = new URLSearchParams({ client_id: env.OIDC_CLIENT_ID, redirect_uri: env.BASE_URL + "/callback",
    response_type: "code", scope: "openid email profile", state, code_challenge: challenge, code_challenge_method: "S256" }).toString();
  return u.toString();
}

/** the code Access sent back → the signed-in account (Access only signs in an address it has verified: a Google
 *  account's, or one an e-mailed code reached) */
export async function exchange(env: Env, code: string, verifier: string): Promise<User> {
  const r = await fetch(endpoint(env, "token"), { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: env.OIDC_CLIENT_ID, client_secret: env.OIDC_CLIENT_SECRET, code,
      code_verifier: verifier, grant_type: "authorization_code", redirect_uri: env.BASE_URL + "/callback" }) });
  if (!r.ok) throw new Error(`the sign-in's token exchange failed (${r.status})`);
  const t = await r.json() as { access_token?: string };
  if (!t.access_token) throw new Error("the sign-in sent no access token");
  const u = await fetch(endpoint(env, "userinfo"), { headers: { authorization: "Bearer " + t.access_token } });
  if (!u.ok) throw new Error(`the sign-in's userinfo failed (${u.status})`);
  const info = await u.json() as { sub?: string; email?: string; name?: string };
  if (!info.email || !info.sub) throw new Error("the sign-in did not name an email address");
  return { sub: info.sub, email: info.email.toLowerCase(), name: info.name || null };
}
