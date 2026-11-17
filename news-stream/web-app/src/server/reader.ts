// Who is reading a page the server renders: the approved account's email, or null. With it the render carries what
// only approved accounts see (the full text, the scores) in the page's HTML, instead of the browser fetching it
// after the page has loaded.
//  - Cloudflare Access sends its signed token (Cf-Access-Jwt-Assertion) on the paths its apps cover: /app/… on the
//    main site, every path of the beta site. There the token is checked as everywhere (access.ts).
//  - A public page of the main site (/, /item/<id>) is outside the Access app: a signed-in reader's request carries
//    only Access's cookie (CF_Authorization, set for the whole host), and no token. The cookie is never judged here.
//    The render asks /app/me on the same site with that cookie and nothing else, so Cloudflare Access decides as it
//    does for the browser's own /app/… requests (its session, a revoked account), and /app/me answers from the token
//    Access then sends. What is for approved accounts is still answered only where Access has said who is asking.
// Asked at most once per request. A request with neither token nor cookie costs nothing, and the page says nothing
// about who reads it: the browser finds out, as it did before the server could.
import { getRequest } from "@tanstack/react-start/server";
import { accessUser, NOT_APPROVED } from "./access";
import { db, requestState, type Sql } from "./db";
import { hostOf } from "./gates";

/** the sites whose public pages are outside Cloudflare Access (the beta site is behind it as a whole) */
const SITES = new Set(["newswatch.yyc-internal.com"]);
const COOKIE = /(?:^|;\s*)CF_Authorization=([^;\s]+)/;
const LOCAL = /^(127\.0\.0\.1|localhost)(:\d+)?$/;
/** how long /app/me may take (it answers in well under a second): after that the page goes out without waiting */
export const ASK_MS = 1500;

export class NotApproved extends Error {
  status = 403;
  constructor() { super(NOT_APPROVED); }
}

/** where this site's /app/me is, or null (a host that is not ours is never asked). Tests: the dev server or a local
 *  preview, reached through the test's stand-in for Access, is asked at the address the stand-in gave (as access.ts
 *  takes the test's keys there); on Cloudflare a request's own host is never local and DEV is false */
function site(req: Request): string | null {
  const host = hostOf(req);
  if (SITES.has(host)) return "https://" + host;
  if ((import.meta.env.DEV || LOCAL.test(new URL(req.url).host)) && LOCAL.test(host)) return "http://" + host;
  return null;
}

/** the approved account, null (nobody: a token that does not hold), or undefined: cannot tell. No cookie, or a cookie
 *  Access did not take, is "cannot tell", never "nobody": the browser's own /app/me is the judge then, as it always
 *  was, so nothing here can turn a signed-in reader's page into a public one */
async function ask(req: Request): Promise<string | null | undefined> {
  if (req.headers.get("Cf-Access-Jwt-Assertion")) return accessUser(req);
  const cookie = COOKIE.exec(req.headers.get("Cookie") || "")?.[1];
  if (!cookie) return undefined;
  const at = site(req);
  if (!at) return undefined;
  try {
    const r = await fetch(at + "/app/me", { headers: { Cookie: "CF_Authorization=" + cookie }, redirect: "manual",
      signal: AbortSignal.timeout(ASK_MS) });
    const email = r.status === 200 ? ((await r.json()) as { email?: string | null }).email : null;
    if (r.status !== 200) await r.body?.cancel().catch(() => undefined);
    return email || undefined;  // Access's sign-in page, a refusal, no answer: not known
  } catch {
    return undefined;
  }
}

/** who is reading this request's page: the approved account's email, null (a token that does not hold), or undefined
 *  (cannot tell: the page is rendered without saying either, and the browser asks /app/me itself) */
export function reader(): Promise<string | null | undefined> {
  const state = requestState();
  if (state?.reader) return state.reader;
  const p = ask(getRequest());
  if (state) {
    state.reader = p;
    void p.then(email => { state.signedIn = !!email; });
  }
  return p;
}

/** a read for approved accounts inside a server render: NotApproved for everyone else */
export async function approvedRead<T>(read: (sql: Sql) => Promise<T>): Promise<T> {
  if (!(await reader())) throw new NotApproved();
  return read(await db());
}
