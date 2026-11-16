// Worker yyc-nametags (nametags/README.md), picked by Host:
// - register.yyc-internal.com (no Access): the public sign-up form and status pages (public.js);
// - tags.yyc-internal.com and anything else, behind the Access app "YYC 名牌報名": the staff page, the staff
//   API and the app's API. Every such request's Cf-Access-Jwt-Assertion is checked here too (team + this app's AUD);
//   staff tokens carry an email, the bridge's service token a common_name.
// The hourly cron writes the day-before reminders. State lives in the Durable Object Store.
import { verifyAccess } from "./auth.js";
import { publicRoutes } from "./public.js";
import { runDupCheck } from "./dup.js";
import { qrSvg } from "./render.js";
import { REGISTER_ORIGIN, checkEvent, checkRegistration, formUrl } from "./rules.js";
import staffHtml from "./pages/staff.html";
export { Store } from "./store.js";

export const W = 848, H = 590;
const MAX_PNG = 1 << 20;
const SCHEME = "dev.de0ch.yycnametags";
const STATE_RE = /^[A-Za-z0-9_-]{16,128}$/;
const PNG_SIG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

const json = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers } });
const err = (status, error) => json({ error }, status);
const body = async (req) => { try { return await req.json(); } catch { return undefined; } };
const GUEST_ACTIONS = new Set(["accept", "reject", "not-duplicate", "merge"]);
const STATES = new Set(["pending", "accepted", "rejected", "merged"]);

// → [width, height] of a PNG, or null when it is not one
export function pngSize(buf) {
  const b = new Uint8Array(buf);
  if (b.length < 24 || PNG_SIG.some((v, i) => b[i] !== v)) return null;
  if (String.fromCharCode(...b.slice(12, 16)) !== "IHDR") return null;
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  return [dv.getUint32(16), dv.getUint32(20)];
}

const cookie = (req, name) => {
  for (const part of (req.headers.get("Cookie") || "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return null;
};

// X-Printer-Error: UTF-8 text percent-encoded by the bridge (e.g. 碳帶已用完); absent = no error
export function printerError(req) {
  const v = req.headers.get("X-Printer-Error");
  if (!v) return null;
  try { return decodeURIComponent(v); } catch { return v; }
}

const str = (v) => (v == null || v === "" ? null : String(v));

// a guest from PUT /api/admin/guests → the stored shape, or a string saying what is wrong
export function cleanGuest(g, i) {
  if (!g || typeof g !== "object") return `guest ${i}: not an object`;
  const id = str(g.id), name = str(g.name);
  if (!id) return `guest ${i}: no id`;
  if (!name) return `guest ${i}: no name`;
  const status = str(g.status);
  if (status !== null && status !== "Sent" && status !== "Withdrawn") return `guest ${i}: status must be Sent, Withdrawn or null`;
  return {
    id, name, institution: str(g.institution), salutation: str(g.salutation), surname: str(g.surname),
    firstname: str(g.firstname), title: str(g.title), email: str(g.email), mobile: str(g.mobile), status,
    table: str(g.table),
  };
}

export default {
  async fetch(req, env, ctx, { fetcher = fetch } = {}) {
    const url = new URL(req.url);
    const path = url.pathname;
    const origin = env.REGISTER_ORIGIN || REGISTER_ORIGIN;
    const store = env.STORE.get(env.STORE.idFromName("main"));
    if (url.hostname === new URL(origin).hostname) return publicRoutes(req, env, ctx, { fetcher, store, origin });

    let who;
    try {
      who = await verifyAccess(req.headers.get("Cf-Access-Jwt-Assertion"), { team: env.ACCESS_TEAM, aud: env.ACCESS_AUD, fetcher });
    } catch (e) {
      return err(e.message === "access not configured" ? 500 : 403, `forbidden: ${e.message}`);
    }
    const service = who.kind === "service";
    const m = req.method;
    let mm;

    if ((path === "/" || path === "/staff" || path.startsWith("/staff/")) && m === "GET")
      return new Response(staffHtml, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Frame-Options": "DENY" } });

    if (path === "/api/app-signin" && m === "GET") {
      if (service) return err(403, "staff sign-in only");
      const state = url.searchParams.get("state") || "";
      if (!STATE_RE.test(state)) return err(400, "state must be 16-128 url-safe characters");
      const token = cookie(req, "CF_Authorization") || req.headers.get("Cf-Access-Jwt-Assertion");
      return new Response(null, { status: 302, headers: { Location: `${SCHEME}://signed-in?state=${state}#token=${token}`, "Cache-Control": "no-store" } });
    }
    if (path === "/api/guests" && m === "GET") {
      const ev = url.searchParams.get("event");
      if (ev && !(await store.event(ev))) return err(404, "no such event");
      return json(await store.guests(ev || null));
    }
    if (path === "/api/checkin" && m === "GET") {
      const found = await store.byCode(url.searchParams.get("code") || "");
      if (!found) return err(404, "找不到此二維碼");
      if (found.guest.state !== "accepted") return err(409, "此報名未確認");
      return json(found);
    }
    if (path === "/api/printer" && m === "GET") return json(await store.printer());

    if (path === "/api/print" && m === "POST") {
      const guest = url.searchParams.get("guest");
      if (!guest) return err(400, "?guest=ID is required");
      const ct = (req.headers.get("Content-Type") || "").split(";")[0].trim().toLowerCase();
      if (ct !== "image/png") return err(415, "body must be image/png");
      const png = await req.arrayBuffer();
      if (png.byteLength > MAX_PNG) return err(413, "png too large");
      const size = pngSize(png);
      if (!size) return err(400, "not a png");
      if (size[0] !== W || size[1] !== H) return err(400, `png must be ${W}x${H}, got ${size[0]}x${size[1]}`);
      if (!(await store.hasGuest(guest))) return err(404, "no such guest");
      return json(await store.addJob({ id: crypto.randomUUID(), byEmail: service ? `service:${who.commonName}` : who.email, guestId: guest, png }));
    }
    if ((mm = path.match(/^\/api\/print\/([^/]+)$/)) && m === "GET") {
      const j = await store.job(decodeURIComponent(mm[1]));
      return j ? json(j) : err(404, "no such job");
    }

    if (path.startsWith("/api/bridge/") || path.startsWith("/api/admin/")) {
      if (!service) return err(403, "service token only");
      if (path === "/api/bridge/next" && m === "GET") {
        const j = await store.next(printerError(req));
        if (!j) return new Response(null, { status: 204, headers: { "Cache-Control": "no-store" } });
        return new Response(j.png, { headers: { "Content-Type": "image/png", "X-Job-Id": j.id, "Cache-Control": "no-store" } });
      }
      if ((mm = path.match(/^\/api\/bridge\/jobs\/([^/]+)$/)) && m === "POST") {
        let body;
        try { body = await req.json(); } catch { return err(400, "body must be JSON"); }
        if (body?.status !== "done" && body?.status !== "failed") return err(400, "status must be done or failed");
        const j = await store.report(decodeURIComponent(mm[1]), { status: body.status, error: str(body.error) }, printerError(req));
        return j ? json(j) : err(404, "no such job");
      }
      if (path === "/api/admin/guests" && m === "PUT") {
        const ev = url.searchParams.get("event");
        if (!ev) return err(400, "?event=ID is required");
        if (!(await store.event(ev))) return err(404, "no such event");
        let body;
        try { body = await req.json(); } catch { return err(400, "body must be JSON"); }
        const list = Array.isArray(body) ? body : body?.guests;
        if (!Array.isArray(list)) return err(400, 'body must be {"guests":[Guest…]}');
        const clean = list.map(cleanGuest);
        const bad = clean.find((g) => typeof g === "string");
        if (bad) return err(400, bad);
        if (new Set(clean.map((g) => g.id)).size !== clean.length) return err(400, "duplicate guest ids");
        return json(await store.putGuests(ev, clean));
      }
    }

    // ---- staff API: a staff sign-in only
    if (path.startsWith("/api/events") || path.startsWith("/api/guests/")) {
      if (service) return err(403, "staff only");
      if (path === "/api/events" && m === "GET") return json({ events: (await store.events()).map((e) => ({ ...e, formUrl: formUrl(e.id, origin) })) });
      if (path === "/api/events" && m === "POST") {
        const f = checkEvent(await body(req), { create: true });
        if (typeof f === "string") return err(400, f);
        const e = await store.createEvent(f);
        return e ? json({ ...e, formUrl: formUrl(e.id, origin) }) : err(409, "an event with this id exists");
      }
      if ((mm = path.match(/^\/api\/events\/([^/]+)(\/[a-z.]+)?$/))) {
        const id = decodeURIComponent(mm[1]), sub = mm[2] || "";
        if (sub === "" && m === "PATCH") {
          const f = checkEvent(await body(req), { create: false });
          if (typeof f === "string") return err(400, f);
          const e = await store.updateEvent(id, f);
          return e ? json({ ...e, formUrl: formUrl(e.id, origin) }) : err(404, "no such event");
        }
        const e = await store.event(id);
        if (!e) return err(404, "no such event");
        if (sub === "/guests" && m === "GET") {
          const state = url.searchParams.get("state");
          if (state && !STATES.has(state)) return err(400, "state must be pending, accepted, rejected or merged");
          return json({ guests: await store.eventGuests(id, state) });
        }
        if (sub === "/guests" && m === "POST") {
          const b = await body(req);
          if (!b || typeof b !== "object") return err(400, "body must be a JSON object");
          const { values, errors } = checkRegistration({ ...b, phone: b.mobile }, { required: false });
          if (errors.length) return err(400, errors.join("\n"));
          const g = await store.addGuest(id, { values, source: "staff", by: who.email });
          ctx.waitUntil(runDupCheck(store, g.id, { key: env.OPENROUTER_API_KEY, fetcher }));
          return json(g);
        }
        if (sub === "/outbox" && m === "GET") return json({ messages: await store.outbox(id) });
        // the form link as a QR code, for posters
        if (sub === "/qr.svg" && m === "GET")
          return new Response(qrSvg(formUrl(id, origin), { label: e.name }), { headers: { "Content-Type": "image/svg+xml", "Cache-Control": "no-store" } });
      }
      if ((mm = path.match(/^\/api\/guests\/([^/]+)\/([a-z-]+)$/)) && m === "POST" && GUEST_ACTIONS.has(mm[2])) {
        const b = mm[2] === "merge" ? await body(req) : {};
        if (mm[2] === "merge" && typeof b?.into !== "string") return err(400, 'body must be {"into": ID}');
        const r = await store.act(decodeURIComponent(mm[1]), mm[2], { by: who.email, into: b?.into });
        if (r.error) return err(r.status, r.error);
        return json(r.guest ?? r);
      }
    }
    return err(404, "not found");
  },

  // hourly: the reminders for events dated tomorrow in Hong Kong
  async scheduled(controller, env, ctx) {
    const store = env.STORE.get(env.STORE.idFromName("main"));
    ctx.waitUntil((async () => console.log(`reminders written: ${await store.reminders(controller.scheduledTime ?? Date.now())}`))());
  },
};
