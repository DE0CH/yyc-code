// Local development only (`npm run dev`, never deployed): the Worker behind a stand-in for Cloudflare Access. Each
// request gets a token signed by a key made at start, for DEV_EMAIL, or for the YYC AI service token when the request
// carries X-Dev-Service; the Worker's own check runs unchanged against this key.
import worker from "../src/index.js";
export { Store } from "../src/store.js";

const TEAM = "dev", AUD = "dev-aud";
let keys;
async function setup() {
  const pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const jwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
  return { pair, jwks: { keys: [{ ...jwk, kid: "dev", alg: "RS256" }] } };
}
const b64u = (b) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const enc = (o) => b64u(new TextEncoder().encode(JSON.stringify(o)));
async function token(claims) {
  const now = Math.floor(Date.now() / 1000);
  const head = enc({ alg: "RS256", kid: "dev" }), body = enc({ iss: `https://${TEAM}.cloudflareaccess.com`, aud: [AUD], exp: now + 3600, iat: now, ...claims });
  return `${head}.${body}.${b64u(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", keys.pair.privateKey, new TextEncoder().encode(`${head}.${body}`)))}`;
}

export default {
  async fetch(req, env, ctx) {
    keys ??= await setup();
    const h = new Headers(req.headers);
    h.set("Cf-Access-Jwt-Assertion", await token(req.headers.get("X-Dev-Service") ? { common_name: "dev-service" } : { email: env.DEV_EMAIL || "staff@yyc.hk" }));
    const fetcher = async () => Response.json(keys.jwks);
    return worker.fetch(new Request(req, { headers: h }), { ...env, ACCESS_TEAM: TEAM, ACCESS_AUD: AUD }, ctx, { fetcher });
  },
};
