// The public routes on register.yyc-internal.com (no Access app in front): the sign-up form of an event, its status
// page with the QR code, and nothing else. Every response is no-store and sends no Referer (the status URL is the code).
import formHtml from "./pages/form.html";
import statusHtml from "./pages/status.html";
import closedHtml from "./pages/closed.html";
import { fill, errorList, qrSvg } from "./render.js";
import { checkRegistration, codeUrl } from "./rules.js";
import { runDupCheck } from "./dup.js";

export const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
const MAX_FORM = 16 * 1024;
const HEADERS = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY" };

const html = (body, status = 200) => new Response(body, { status, headers: { "Content-Type": "text/html; charset=utf-8", ...HEADERS } });
const notFound = () => new Response("找不到此頁 / Not found\n", { status: 404, headers: { "Content-Type": "text/plain; charset=utf-8", ...HEADERS } });

// Turnstile siteverify → true when the token is good (a missing secret fails closed)
export async function turnstileOk(token, ip, { secret, fetcher = fetch }) {
  if (!secret) { console.error("TURNSTILE_SECRET is not set"); return false; }
  if (!token) return false;
  const body = new FormData();
  body.set("secret", secret);
  body.set("response", token);
  if (ip) body.set("remoteip", ip);
  try {
    const r = await fetcher(SITEVERIFY_URL, { method: "POST", body });
    return r.ok && (await r.json()).success === true;
  } catch (e) {
    console.error("siteverify", e.message);
    return false;
  }
}

// the status page's {{state}}: rejected and merged both show as declined, without detail
export const pageState = (state) => (state === "accepted" || state === "pending" ? state : "declined");
const names = (e) => ({ event_name: e?.name ?? "", event_name_en: e?.nameEn ?? "" });
const closed = (e, status) => html(fill(closedHtml, names(e)), status);
const form = (e, env, v = {}, errors = [], status = 200) => html(fill(formHtml, {
  ...names(e), event_date: e.date, event_venue: e.venue ?? "", event_id: e.id, turnstile_sitekey: env.TURNSTILE_SITEKEY,
  v_name: v.name, v_institution: v.institution, v_email: v.email, v_phone: v.phone,
}, { errors: errorList(errors) }), status);

export async function publicRoutes(req, env, ctx, { fetcher, store, origin }) {
  const path = new URL(req.url).pathname, m = req.method;
  let mm;
  if ((mm = path.match(/^\/e\/([^/]+)\/?$/)) && (m === "GET" || m === "POST")) {
    const e = await store.event(decodeURIComponent(mm[1]));
    if (!e) return closed(null, 404);
    if (!e.open) return closed(e, 200);
    if (m === "GET") return form(e, env);
    if (Number(req.headers.get("Content-Length") || 0) > MAX_FORM) return html("Too large", 413);
    let fd;
    try { fd = await req.formData(); } catch { return form(e, env, {}, ["表格無法讀取，請再試 / The form could not be read, please try again"], 400); }
    const get = (k) => { const v = fd.get(k); return typeof v === "string" ? v : ""; };
    const { values, errors } = checkRegistration({ name: get("name"), institution: get("institution"), email: get("email"), phone: get("phone") });
    if (errors.length) return form(e, env, values, errors, 400);
    if (!(await turnstileOk(get("cf-turnstile-response"), req.headers.get("CF-Connecting-IP"), { secret: env.TURNSTILE_SECRET, fetcher })))
      return form(e, env, values, ["未能確認您不是機械人，請再試 / We could not verify you are human, please try again"], 400);
    const g = await store.addGuest(e.id, { values, source: "form" });
    ctx.waitUntil(runDupCheck(store, g.id, { key: env.OPENROUTER_API_KEY, fetcher }));
    return new Response(null, { status: 303, headers: { Location: `/r/${g.code}`, ...HEADERS } });
  }
  if ((mm = path.match(/^\/r\/([A-Za-z0-9_-]{22})(\/qr\.svg)?$/)) && m === "GET") {
    const found = await store.byCode(mm[1]);
    if (!found) return notFound();
    const { guest, event } = found;
    const svg = guest.state === "accepted" ? qrSvg(codeUrl(guest.code, origin), { label: guest.name }) : "";
    if (mm[2]) return svg ? new Response(svg, { headers: { "Content-Type": "image/svg+xml", ...HEADERS } }) : notFound();
    return html(fill(statusHtml, { ...names(event), guest_name: guest.name, state: pageState(guest.state) }, { qr_svg: svg }));
  }
  return notFound();
}
