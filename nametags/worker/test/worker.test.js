// The Worker end to end under Node: real JWT signatures (a test RSA key served as the team's certs), the Durable Object
// on node:sqlite behind a shim of ctx.storage.sql, Turnstile and OpenRouter stubbed in the fetcher, ctx.waitUntil
// collected so a test can wait for the background duplicate check. Run: npm test (Node 22+).
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import worker, { pngSize, printerError, cleanGuest } from "../src/index.js";
import { Store, PRINTING_TIMEOUT_MS, FIRST_EVENT } from "../src/store.js";
import { resetKeyCache, verifyAccess } from "../src/auth.js";
import { MODEL, OPENROUTER_URL } from "../src/dup.js";
import { SITEVERIFY_URL, pageState } from "../src/public.js";
import { fill, qrSvg } from "../src/render.js";
import { checkRegistration, checkEvent, phoneKey, emailKey, hkDate, nextDay, newCode } from "../src/rules.js";
import qrcodegen from "../src/qrcodegen.js";

const TEAM = "yyc-hk", AUD = "test-aud";
const ISS = `https://${TEAM}.cloudflareaccess.com`;
const TAGS = "https://tags.yyc-internal.com", REG = "https://register.yyc-internal.com";

// ---- keys and tokens
const kp = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const other = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const jwk = { ...(await crypto.subtle.exportKey("jwk", kp.publicKey)), kid: "k1" };
const enc = (o) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");
async function sign(claims, { key = kp.privateKey, kid = "k1", alg = "RS256" } = {}) {
  const h = enc({ alg, kid, typ: "JWT" }), p = enc(claims);
  const s = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(h + "." + p));
  return h + "." + p + "." + Buffer.from(s).toString("base64url");
}
const now = () => Math.floor(Date.now() / 1000);
const userTok = (extra = {}) => sign({ iss: ISS, aud: [AUD], email: "Staff@YYC.hk", exp: now() + 3600, iat: now(), ...extra });
const svcTok = (extra = {}) => sign({ iss: ISS, aud: [AUD], common_name: "bridge.access", exp: now() + 3600, iat: now(), ...extra });

// ---- the outside world: Access certs, Turnstile siteverify, OpenRouter
let certFetches = 0, turnstile, llm, llmCalls, verifyCalls;
const fetcher = async (u, init = {}) => {
  if (u === `${ISS}/cdn-cgi/access/certs`) { certFetches++; return new Response(JSON.stringify({ keys: [jwk] })); }
  if (u === SITEVERIFY_URL) {
    verifyCalls.push(Object.fromEntries(init.body));
    return new Response(JSON.stringify({ success: turnstile(init.body.get("response")) }));
  }
  if (u === OPENROUTER_URL) {
    const req = JSON.parse(init.body);
    llmCalls.push({ headers: init.headers, body: req, input: JSON.parse(req.messages[1].content) });
    return llm(JSON.parse(req.messages[1].content));
  }
  throw new Error(`unexpected fetch ${u}`);
};
const llmAnswer = (duplicates) => new Response(JSON.stringify({ choices: [{ message: { role: "assistant", content: JSON.stringify({ duplicates }) } }] }));

// ---- Durable Object on node:sqlite
function fakeCtx(db = new DatabaseSync(":memory:")) {
  const conv = (v) => (v instanceof ArrayBuffer ? new Uint8Array(v) : v);
  const sql = {
    exec(q, ...b) {
      const rows = db.prepare(q).all(...b.map(conv));
      return { toArray: () => rows, one: () => rows[0] };
    },
  };
  return { db, storage: { sql, transactionSync(fn) { db.exec("BEGIN"); try { const r = fn(); db.exec("COMMIT"); return r; } catch (e) { db.exec("ROLLBACK"); throw e; } } } };
}
let env, store, pending;
const ctx = { waitUntil: (p) => pending.push(p) };
const settle = async () => { while (pending.length) await pending.shift(); };
beforeEach(() => {
  resetKeyCache();
  store = new Store(fakeCtx(), {});
  env = { ACCESS_TEAM: TEAM, ACCESS_AUD: AUD, STORE: { idFromName: (n) => n, get: () => store },
    REGISTER_ORIGIN: REG, TURNSTILE_SITEKEY: "site-key-1", TURNSTILE_SECRET: "ts-secret", OPENROUTER_API_KEY: "or-key" };
  pending = []; llmCalls = []; verifyCalls = [];
  turnstile = (t) => t === "good-token";
  llm = () => llmAnswer([]);
});

async function call(path, { method = "GET", token, headers = {}, body, host = TAGS } = {}) {
  const h = new Headers(headers);
  if (token !== undefined) h.set("Cf-Access-Jwt-Assertion", token);
  return worker.fetch(new Request(host + path, { method, headers: h, body, redirect: "manual" }), env, ctx, { fetcher });
}
const send = async (method, path, body, token) => call(path, { method, token: token ?? (await userTok()), headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const reg = (path, opts = {}) => call(path, { ...opts, host: REG });
const submit = (event, fields) => reg(`/e/${event}`, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
  body: new URLSearchParams({ "cf-turnstile-response": "good-token", ...fields }).toString() });
const codeOf = (r) => r.headers.get("Location").split("/r/")[1];

function png(w = 848, h = 590) {
  const b = new Uint8Array(64);
  b.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 0x49, 0x48, 0x44, 0x52]);
  const dv = new DataView(b.buffer); dv.setUint32(16, w); dv.setUint32(20, h); b[24] = 1;
  return b;
}
const guests = [
  { id: "1", name: "Jing HUANG", institution: "Ministry of Service and Technology", salutation: "Ms", surname: "HUANG", firstname: "Jing", title: "Director", email: "j@example.com", mobile: "85291234567", status: "Sent" },
  { id: "2", name: "Ki Cheung Otto WONG", institution: null, status: "Withdrawn" },
];
const day = (offset) => nextDayN(hkDate(Date.now()), offset);
const nextDayN = (d, n) => { for (let i = 0; i < n; i++) d = nextDay(d); if (n < 0) { const x = new Date(d + "T00:00:00Z"); x.setUTCDate(x.getUTCDate() + n); d = x.toISOString().slice(0, 10); } return d; };
const mkEvent = (id = "sff", extra = {}) => store.createEvent({ id, name: "論壇 " + id, nameEn: "Forum " + id, date: day(30), venue: "HKCEC", mode: "review", open: true, ...extra });
const putGuests = async (list = guests, event = "sff") => {
  if (!store.event(event)) mkEvent(event);
  return call(`/api/admin/guests?event=${event}`, { method: "PUT", token: await svcTok(), headers: { "Content-Type": "application/json" }, body: JSON.stringify({ guests: list }) });
};

// ---- auth
test("no token, wrong audience, issuer, signature, alg, expiry are all refused", async () => {
  assert.equal((await call("/api/guests")).status, 403);
  assert.equal((await call("/api/guests", { token: "garbage" })).status, 403);
  assert.equal((await call("/api/guests", { token: await userTok({ aud: ["other"] }) })).status, 403);
  assert.equal((await call("/api/guests", { token: await userTok({ iss: "https://evil.cloudflareaccess.com" }) })).status, 403);
  assert.equal((await call("/api/guests", { token: await userTok({}, ) .then((t) => t.slice(0, -4) + "AAAA") })).status, 403);
  assert.equal((await call("/api/guests", { token: await sign({ iss: ISS, aud: [AUD], email: "a@b", exp: now() + 60 }, { key: other.privateKey }) })).status, 403);
  assert.equal((await call("/api/guests", { token: await sign({ iss: ISS, aud: [AUD], email: "a@b", exp: now() + 60 }, { alg: "none" }) })).status, 403);
  assert.equal((await call("/api/guests", { token: await userTok({ exp: now() - 3600 }) })).status, 403);
  assert.equal((await call("/api/guests", { token: await sign({ iss: ISS, aud: [AUD], exp: now() + 60 }) })).status, 403);
  assert.equal((await call("/api/guests", { token: await userTok() })).status, 200);
});

test("unconfigured AUD fails closed", async () => {
  env.ACCESS_AUD = "";
  assert.equal((await call("/api/guests", { token: await userTok() })).status, 500);
});

test("certs are cached across requests", async () => {
  certFetches = 0;
  await call("/api/guests", { token: await userTok() });
  await call("/api/guests", { token: await userTok() });
  assert.equal(certFetches, 1);
});

test("verifyAccess returns the identity", async () => {
  assert.deepEqual(await verifyAccess(await userTok(), { team: TEAM, aud: AUD, fetcher }), { kind: "user", email: "staff@yyc.hk" });
  assert.deepEqual(await verifyAccess(await svcTok(), { team: TEAM, aud: AUD, fetcher }), { kind: "service", commonName: "bridge.access" });
});

// ---- hosts
test("the register host serves only the public routes, with no Access check", async () => {
  mkEvent();
  certFetches = 0;
  assert.equal((await reg("/e/sff")).status, 200);
  for (const p of ["/", "/staff", "/api/guests", "/api/events", "/api/printer", "/e", "/r/short", "/e/sff/x"]) {
    const r = await reg(p, { token: await userTok() });
    assert.equal(r.status, 404, p);
    assert.equal(r.headers.get("Referrer-Policy"), "no-referrer");
  }
  assert.equal((await reg("/e/sff", { method: "PUT" })).status, 404);
  assert.equal(certFetches, 0, "no Access check on the register host");
});

test("the staff page: / and /staff/* on the tags host, behind the Access check", async () => {
  assert.equal((await call("/")).status, 403);
  for (const p of ["/", "/staff", "/staff/events/sff"]) {
    const r = await call(p, { token: await userTok() });
    assert.equal(r.status, 200, p);
    assert.match(r.headers.get("Content-Type"), /^text\/html/);
    assert.match(await r.text(), /<html|<!doctype/i);
  }
  assert.equal((await call("/other", { token: await userTok() })).status, 404);
});

// ---- migration
test("migration: the old guest list becomes event surgical-futures-forum, once", () => {
  const c = fakeCtx();
  c.storage.sql.exec(`CREATE TABLE guests (pos INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, data TEXT NOT NULL)`);
  c.storage.sql.exec(`CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)`);
  guests.forEach((g, i) => c.storage.sql.exec(`INSERT INTO guests (pos, id, data) VALUES (?, ?, ?)`, i, g.id, JSON.stringify({ table: null, ...g })));
  c.storage.sql.exec(`INSERT INTO meta (k, v) VALUES ('guests_updated', '2026-10-29T10:00:00.000Z')`);
  c.storage.sql.exec(`CREATE TABLE jobs (id TEXT PRIMARY KEY, created INTEGER NOT NULL, by_email TEXT, guest_id TEXT, png BLOB, status TEXT NOT NULL, error TEXT, started INTEGER, finished INTEGER)`);

  const s = new Store(c, {});
  const e = s.event("surgical-futures-forum");
  assert.deepEqual({ ...e, created: undefined }, { ...FIRST_EVENT, created: undefined });
  assert.equal(e.date, "2026-11-09");
  const list = s.guests("surgical-futures-forum");
  assert.equal(list.updated, "2026-10-29T10:00:00.000Z");
  assert.deepEqual(list.guests.map((g) => g.id), ["surgical-futures-forum:1", "surgical-futures-forum:2"]);
  const g = list.guests[0];
  assert.equal(g.name, "Jing HUANG");
  assert.equal(g.mobile, "85291234567");
  assert.equal(g.status, "Sent");
  assert.deepEqual([g.event, g.source, g.state, g.mergedInto, g.dup.state], ["surgical-futures-forum", "import", "accepted", null, "none"]);
  assert.match(g.code, /^[A-Za-z0-9_-]{22}$/);
  assert.notEqual(g.code, list.guests[1].code);
  assert.equal(c.db.prepare(`SELECT COUNT(*) AS n FROM sqlite_master WHERE name = 'guests'`).get().n, 0, "old table dropped");
  assert.equal(s.outbox("surgical-futures-forum").length, 0, "no confirmations for imported guests");

  const again = new Store(c, {});
  assert.equal(again.guests("surgical-futures-forum").guests.length, 2);
  assert.equal(again.guests("surgical-futures-forum").guests[0].code, g.code);
});

test("migration: a fresh store has no events", () => {
  assert.deepEqual(store.events(), []);
});

// ---- the app's guest list
test("/api/guests: accepted guests of the event; without ?event the soonest upcoming, else the latest", async () => {
  let r = await call("/api/guests", { token: await userTok() });
  assert.deepEqual(await r.json(), { guests: [], updated: null, event: null });
  mkEvent("past-one", { date: day(-10) });
  mkEvent("past-two", { date: day(-3) });
  assert.equal((await (await call("/api/guests", { token: await userTok() })).json()).event.id, "past-two");
  mkEvent("far", { date: day(40) });
  mkEvent("today", { date: day(0) });
  assert.equal((await (await call("/api/guests", { token: await svcTok() })).json()).event.id, "today");
  await putGuests(guests, "far");
  r = await call("/api/guests?event=far", { token: await userTok() });
  const body = await r.json();
  assert.equal(body.event.id, "far");
  assert.equal(body.guests.length, 2);
  assert.deepEqual(Object.keys(body.guests[0]).slice(0, 11), ["id", "name", "institution", "salutation", "surname", "firstname", "title", "email", "mobile", "status", "table"]);
  assert.equal(body.guests[1].status, "Withdrawn");
  assert.match(body.updated, /^\d{4}-\d\d-\d\dT/);
  assert.equal((await call("/api/guests?event=nope", { token: await userTok() })).status, 404);

  await submit("far", { name: "New Person", email: "n@example.com", phone: "61234567" });
  await settle();
  assert.equal((await (await call("/api/guests?event=far", { token: await userTok() })).json()).guests.length, 2, "pending not listed");
});

// ---- admin import
test("admin guests: service token only, ?event required, replaces that event's imported guests only", async () => {
  mkEvent();
  assert.equal((await call("/api/admin/guests?event=sff", { method: "PUT", token: await userTok(), body: "{}" })).status, 403);
  assert.equal((await call("/api/admin/guests", { method: "PUT", token: await svcTok(), body: JSON.stringify({ guests }) })).status, 400);
  assert.equal((await call("/api/admin/guests?event=nope", { method: "PUT", token: await svcTok(), body: JSON.stringify({ guests }) })).status, 404);
  let r = await putGuests();
  assert.equal(r.status, 200);
  assert.equal((await r.json()).count, 2);
  const first = store.guest("sff:1");
  store.act("sff:2", "reject", { by: "a@yyc.hk" });
  await submit("sff", { name: "Form Person", email: "f@example.com", phone: "61234567" });
  await settle();
  mkEvent("other");
  await putGuests([{ id: "1", name: "Other One" }], "other");

  await putGuests([{ ...guests[1], name: "Otto WONG" }, { ...guests[0], institution: "MST" }, { id: "3", name: "Three" }]);
  const list = store.eventGuests("sff");
  assert.deepEqual(list.map((g) => g.id).slice(0, 3), ["sff:2", "sff:1", "sff:3"], "sheet order");
  assert.equal(list.length, 4, "the form entry stays");
  const again = store.guest("sff:1");
  assert.equal(again.code, first.code, "a re-imported guest keeps its code");
  assert.equal(again.institution, "MST");
  assert.equal(store.guest("sff:2").state, "rejected", "and its state");
  assert.equal(store.guest("sff:2").name, "Otto WONG");
  await putGuests([guests[0]]);
  assert.equal(store.guest("sff:2"), null, "dropped from the sheet = gone");
  assert.equal(store.guest("other:1").name, "Other One", "other events untouched");
});

test("admin guests: bad input is refused and the old list stays", async () => {
  await putGuests();
  for (const list of [[{ name: "x" }], [{ id: "1" }], [{ id: "1", name: "a", status: "Maybe" }], [{ id: "1", name: "a" }, { id: "1", name: "b" }]])
    assert.equal((await putGuests(list)).status, 400);
  assert.equal((await call("/api/admin/guests?event=sff", { method: "PUT", token: await svcTok(), body: "nope" })).status, 400);
  assert.equal((await (await call("/api/guests?event=sff", { token: await userTok() })).json()).guests.length, 2);
});

test("cleanGuest stringifies numbers and empties to null", () => {
  assert.deepEqual(cleanGuest({ id: 7, name: "A", mobile: 85291234567, email: "" }, 0).mobile, "85291234567");
  assert.equal(cleanGuest({ id: 7, name: "A", email: "" }, 0).email, null);
});

// ---- events
test("events: create, 409 on a taken id, patch, list with counts and the form link; staff only", async () => {
  const ev = { id: "gala-2026", name: "周年晚宴", nameEn: "Annual Gala", date: "2026-12-01", venue: "Central", mode: "auto", open: true };
  let r = await send("POST", "/api/events", ev);
  assert.equal(r.status, 200);
  const e = await r.json();
  assert.deepEqual({ ...e, created: undefined }, { ...ev, created: undefined, formUrl: `${REG}/e/gala-2026` });
  assert.match(e.created, /Z$/);
  assert.equal((await send("POST", "/api/events", ev)).status, 409);
  assert.equal((await send("POST", "/api/events", ev, await svcTok())).status, 403);
  for (const bad of [{ ...ev, id: "AB" }, { ...ev, id: "x".repeat(41) }, { ...ev, id: "a_b" }, { ...ev, name: "" }, { ...ev, date: "2026-02-30" },
    { ...ev, date: "1/12/2026" }, { ...ev, mode: "open" }, { ...ev, open: "yes" }, { ...ev, venue: 5 }])
    assert.equal((await send("POST", "/api/events", { ...bad, id: bad.id === ev.id ? "new-one" : bad.id })).status, 400, JSON.stringify(bad));
  assert.equal((await send("POST", "/api/events", { id: "min", name: "Minimal", date: "2026-12-02", mode: "review", open: false })).status, 200);
  assert.equal(store.event("min").nameEn, null);

  r = await send("PATCH", "/api/events/gala-2026", { open: false, venue: null, mode: "review" });
  assert.equal(r.status, 200);
  assert.deepEqual([(await r.json()).open, store.event("gala-2026").venue, store.event("gala-2026").mode], [false, null, "review"]);
  assert.equal((await send("PATCH", "/api/events/gala-2026", { id: "x" })).status, 400);
  assert.equal((await send("PATCH", "/api/events/gala-2026", {})).status, 400);
  assert.equal((await send("PATCH", "/api/events/nope", { open: true })).status, 404);

  r = await call("/api/events", { token: await userTok() });
  const { events } = await r.json();
  assert.deepEqual(events.map((x) => x.id), ["min", "gala-2026"], "newest date first");
  assert.deepEqual(events[1].counts, { pending: 0, flagged: 0, accepted: 0, rejected: 0, outbox: 0 });
  assert.equal(events[1].formUrl, `${REG}/e/gala-2026`);
  assert.equal((await call("/api/events", { token: await svcTok() })).status, 403);
});

test("events: the form link as a QR SVG", async () => {
  mkEvent();
  const r = await call("/api/events/sff/qr.svg", { token: await userTok() });
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("Content-Type"), "image/svg+xml");
  assert.match(await r.text(), /^<svg [^>]*viewBox="0 0 \d+ \d+"/);
  assert.equal((await call("/api/events/nope/qr.svg", { token: await userTok() })).status, 404);
});

test("events: counts follow the registrations and the outbox", async () => {
  mkEvent();
  await putGuests();
  await submit("sff", { name: "A Person", email: "a@example.com", phone: "61111111" });
  await submit("sff", { name: "B Person", email: "J@Example.com ", phone: "62222222" }); // same email as sheet guest 1
  await submit("sff", { name: "C Person", email: "c@example.com", phone: "63333333" });
  await settle();
  const c = store.eventGuests("sff", "pending").find((g) => g.name === "C Person");
  store.act(c.id, "reject", { by: "s@yyc.hk" });
  const a = store.eventGuests("sff", "pending").find((g) => g.name === "A Person");
  store.act(a.id, "accept", { by: "s@yyc.hk" });
  const { events } = await (await call("/api/events", { token: await userTok() })).json();
  assert.deepEqual(events[0].counts, { pending: 1, flagged: 1, accepted: 3, rejected: 1, outbox: 2 });
});

// ---- public form
test("form: unknown event 404, closed event says so, open event renders the template", async () => {
  let r = await reg("/e/nope");
  assert.equal(r.status, 404);
  assert.match(r.headers.get("Content-Type"), /^text\/html/);
  mkEvent("shut", { open: false, name: "已截止<活動>" });
  r = await reg("/e/shut");
  assert.equal(r.status, 200);
  const shut = await r.text();
  assert.ok(shut.includes("已截止&lt;活動&gt;"));
  assert.ok(!shut.includes('name="name"'), "no form on the closed page");
  assert.equal((await submit("shut", { name: "X", email: "x@example.com", phone: "61234567" })).status, 200);
  assert.equal(store.eventGuests("shut").length, 0);

  mkEvent();
  r = await reg("/e/sff");
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("Cache-Control"), "no-store");
  assert.equal(r.headers.get("Referrer-Policy"), "no-referrer");
  const page = await r.text();
  for (const s of ["論壇 sff", "Forum sff", day(30), "HKCEC", 'action="/e/sff"', 'data-sitekey="site-key-1"']) assert.ok(page.includes(s), s);
  assert.ok(!/\{\{/.test(page), "every placeholder filled");
});

test("form: errors re-render the form with the values, before any Turnstile call", async () => {
  mkEvent();
  const r = await submit("sff", { name: " ", institution: "x".repeat(201), email: "not-an-email", phone: "12-34" });
  assert.equal(r.status, 400);
  const page = await r.text();
  for (const s of ["請填寫姓名", "Institution must be at most 200", "電郵格式不正確", "Phone must be 6 to 20 digits"]) assert.ok(page.includes(s), s);
  assert.ok(page.includes('value="not-an-email"'));
  assert.equal(verifyCalls.length, 0);
  const xss = await (await submit("sff", { name: '"><script>x</script>', email: "", phone: "" })).text();
  assert.ok(!xss.includes("<script>x"), "values are escaped");
  assert.ok(xss.includes("&quot;&gt;&lt;script&gt;"));
  assert.ok(xss.includes("請填寫電郵") && xss.includes("請填寫電話"));
  assert.equal(store.eventGuests("sff").length, 0);
});

test("form: a failed or missing Turnstile check re-renders; the secret is sent with the client IP", async () => {
  mkEvent();
  turnstile = () => false;
  let r = await submit("sff", { name: "A", email: "a@example.com", phone: "+852 6123-4567" });
  assert.equal(r.status, 400);
  assert.ok((await r.text()).includes("未能確認您不是機械人"));
  assert.deepEqual(verifyCalls[0], { secret: "ts-secret", response: "good-token" });
  env.TURNSTILE_SECRET = "";
  turnstile = () => true;
  assert.equal((await submit("sff", { name: "A", email: "a@example.com", phone: "61234567" })).status, 400, "no secret fails closed");
  env.TURNSTILE_SECRET = "ts-secret";
  r = await reg("/e/sff", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded", "CF-Connecting-IP": "203.0.113.9" },
    body: new URLSearchParams({ name: "A", email: "a@example.com", phone: "61234567", "cf-turnstile-response": "any" }).toString() });
  assert.equal(r.status, 303);
  assert.equal(verifyCalls.at(-1).remoteip, "203.0.113.9");
  assert.equal(store.eventGuests("sff").length, 1);
});

test("form → 303 /r/:code → status page: pending, accepted with the QR, declined", async () => {
  mkEvent();
  const r = await submit("sff", { name: "陳大文", institution: "", email: "TaiMan@Example.com", phone: "(852) 9123 4567" });
  assert.equal(r.status, 303);
  const code = codeOf(r);
  assert.match(code, /^[A-Za-z0-9_-]{22}$/);
  await settle();
  const g = store.byCode(code).guest;
  assert.deepEqual([g.name, g.institution, g.email, g.mobile, g.source, g.state, g.dup.state], ["陳大文", null, "TaiMan@Example.com", "(852) 9123 4567", "form", "pending", "none"]);

  const qr = qrSvg(`${REG}/r/${code}`, { label: "陳大文" });
  let page = await (await reg(`/r/${code}`)).text();
  assert.ok(page.includes("陳大文") && page.includes("論壇 sff"));
  assert.ok(!page.includes(qr));
  assert.equal((await reg(`/r/${code}/qr.svg`)).status, 404);

  store.act(g.id, "accept", { by: "s@yyc.hk" });
  page = await (await reg(`/r/${code}`)).text();
  assert.ok(page.includes(qr));
  const svg = await reg(`/r/${code}/qr.svg`);
  assert.equal(svg.status, 200);
  assert.equal(svg.headers.get("Content-Type"), "image/svg+xml");
  assert.equal(await svg.text(), qr);

  store.act(g.id, "reject", { by: "s@yyc.hk" });
  page = await (await reg(`/r/${code}`)).text();
  assert.ok(!page.includes(qr));
  assert.equal((await reg(`/r/${code}/qr.svg`)).status, 404);
  assert.equal((await reg(`/r/${newCode()}`)).status, 404);
});

test("status page: {{state}} is pending, accepted or declined (rejected and merged)", async () => {
  mkEvent();
  const codes = [];
  for (const n of ["A", "B", "C", "D"]) codes.push(codeOf(await submit("sff", { name: n, email: `${n}@example.com`, phone: `6${n.charCodeAt(0)}00000` })));
  await settle();
  const id = (i) => store.byCode(codes[i]).guest.id;
  store.act(id(1), "accept", { by: "s" });
  store.act(id(2), "reject", { by: "s" });
  store.act(id(3), "merge", { by: "s", into: id(1) });
  assert.deepEqual(codes.map((c) => pageState(store.byCode(c).guest.state)), ["pending", "accepted", "declined", "declined"]);
  for (const [i, c] of codes.entries()) {
    const page = await (await reg(`/r/${c}`)).text();
    assert.equal(page.includes(`aria-label="${["A", "B", "C", "D"][i]}"`), i === 1, "the QR only when accepted");
    assert.ok(!/\{\{/.test(page), "every placeholder filled");
  }
});

// ---- duplicate check
test("dup: the same email or phone (HK with or without 852) is flagged at once, also in auto mode", async () => {
  mkEvent("sff", { mode: "auto" });
  await putGuests();
  llm = () => new Promise(() => {}); // the LLM never answers: the deterministic flag is already there
  await submit("sff", { name: "Someone", email: " J@EXAMPLE.com", phone: "61111111" });
  await submit("sff", { name: "Other", email: "o@example.com", phone: "9123 4567" });
  const [a, b] = store.eventGuests("sff", "pending");
  assert.deepEqual(a.dup, { state: "flagged", candidates: [{ id: "sff:1", reason: "相同電郵" }] });
  assert.deepEqual(b.dup, { state: "flagged", candidates: [{ id: "sff:1", reason: "相同電話" }] });
  pending = [];
  store.finishDup(a.id, { candidates: [] });
  assert.equal(store.guest(a.id).state, "pending", "flagged stays pending in auto mode");
  assert.equal(store.guest(a.id).dup.state, "flagged");
});

test("dup: rejected and merged entries don't count", async () => {
  mkEvent();
  await putGuests();
  store.act("sff:1", "reject", { by: "s" });
  await submit("sff", { name: "Someone", email: "j@example.com", phone: "61111111" });
  await settle();
  assert.equal(store.eventGuests("sff", "pending")[0].dup.state, "none");
  assert.equal(llmCalls[0].input.others.length, 1, "only the live sheet guest is sent");
  assert.equal(llmCalls[0].input.others[0].id, "sff:2");
});

test("dup: the OpenRouter request, and its candidates flag the entry", async () => {
  mkEvent("sff", { mode: "auto" });
  await putGuests();
  llm = (input) => llmAnswer([{ id: "sff:1", reason: "中英文姓名相同" }, { id: "not-in-list", reason: "x" }, { id: input.new.id, reason: "self" }]);
  await submit("sff", { name: "黃靜", institution: "MST", email: "huang.jing@example.org", phone: "61234567" });
  await settle();
  const g = store.eventGuests("sff", "pending")[0];
  assert.deepEqual(g.dup, { state: "flagged", candidates: [{ id: "sff:1", reason: "中英文姓名相同" }] });
  assert.equal(g.state, "pending");
  assert.equal(llmCalls.length, 1);
  const { headers, body, input } = llmCalls[0];
  assert.equal(headers.Authorization, "Bearer or-key");
  assert.equal(body.model, MODEL);
  assert.equal(MODEL, "deepseek/deepseek-v4.1-flash");
  assert.deepEqual(body.reasoning, { effort: "low", exclude: true });
  assert.equal(body.response_format.type, "json_schema");
  assert.equal(body.response_format.json_schema.strict, true);
  assert.deepEqual(body.response_format.json_schema.schema.required, ["duplicates"]);
  assert.deepEqual(body.provider, { require_parameters: true });
  assert.deepEqual(input.new, { id: g.id, name: "黃靜", institution: "MST", email: "huang.jing@example.org", phone: "61234567" });
  assert.deepEqual(input.others.map((o) => o.id), ["sff:1", "sff:2"]);
  assert.deepEqual(Object.keys(input.others[0]), ["id", "name", "institution", "email", "phone"]);
});

test("dup: auto mode accepts an unflagged entry and writes the confirmation; review mode leaves it pending", async () => {
  mkEvent("auto-ev", { mode: "auto" });
  mkEvent("rev-ev", { mode: "review" });
  for (const e of ["auto-ev", "rev-ev"]) await putGuests([{ id: "9", name: "Someone Else" }], e);
  await submit("auto-ev", { name: "Amy Lee", email: "amy@example.com", phone: "61234567" });
  await submit("rev-ev", { name: "Amy Lee", email: "amy@example.com", phone: "61234567" });
  await settle();
  const [a] = store.eventGuests("auto-ev", "accepted").filter((g) => g.source === "form");
  assert.deepEqual([a.state, a.dup.state, a.by], ["accepted", "none", "auto"]);
  const out = store.outbox("auto-ev");
  assert.deepEqual(out.map((m) => [m.kind, m.channel, m.to, m.status]).sort(), [["confirmation", "email", "amy@example.com", "off"], ["confirmation", "whatsapp", "+85261234567", "off"]]);
  assert.ok(out.find((m) => m.channel === "email").subject.includes("論壇 auto-ev"));
  assert.equal(out.find((m) => m.channel === "whatsapp").subject, null);
  assert.ok(out[0].body.includes(`${REG}/r/${a.code}`) && out[0].body.includes("Dear Amy Lee"));
  assert.equal(store.eventGuests("rev-ev", "pending").length, 1);
  assert.equal(store.outbox("rev-ev").length, 0);
});

test("dup: an OpenRouter failure, a bad answer or no key → dup.state error, never blocking (auto still accepts)", async () => {
  mkEvent("sff", { mode: "auto" });
  await putGuests([{ id: "9", name: "Someone Else" }]);
  const answers = [() => new Response("busy", { status: 503 }), () => new Response(JSON.stringify({ choices: [{ message: { content: "no json" } }] })),
    () => { throw new Error("timeout"); }];
  for (const [i, a] of answers.entries()) {
    llm = a;
    await submit("sff", { name: `P${i}`, email: `p${i}@example.com`, phone: `6123456${i}` });
    await settle();
  }
  env.OPENROUTER_API_KEY = "";
  await submit("sff", { name: "P9", email: "p9@example.com", phone: "61234569" });
  await settle();
  const form = store.eventGuests("sff").filter((g) => g.source === "form");
  assert.equal(form.length, 4);
  for (const g of form) {
    assert.equal(g.dup.state, "error", g.name);
    assert.equal(g.state, "accepted", g.name);
  }
  assert.match(form[3].dup.error, /OPENROUTER_API_KEY/);
});

test("dup: the first entry of an event needs no LLM call", async () => {
  mkEvent("sff", { mode: "auto" });
  await submit("sff", { name: "First", email: "f@example.com", phone: "61234567" });
  await settle();
  assert.equal(llmCalls.length, 0);
  assert.equal(store.eventGuests("sff")[0].state, "accepted");
});

// ---- staff actions
test("staff actions: accept, reject, not-duplicate, merge; 409 when they don't apply; by and at recorded", async () => {
  mkEvent();
  await putGuests();
  await submit("sff", { name: "Jing Huang", email: "j@example.com", phone: "61111111" }); // flagged against sff:1
  await submit("sff", { name: "Bob", email: "b@example.com", phone: "62222222" });
  await settle();
  const [dupe, bob] = store.eventGuests("sff", "pending");
  const t = await userTok();
  const post = (id, action, body) => call(`/api/guests/${id}/${action}`, { method: "POST", token: t, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body) });

  assert.equal((await post(bob.id, "accept", null)).status === 200, true);
  const acc = store.guest(bob.id);
  assert.deepEqual([acc.state, acc.by], ["accepted", "staff@yyc.hk"]);
  assert.match(acc.at, /Z$/);
  assert.equal((await post(bob.id, "accept")).status, 409);
  assert.equal(store.outbox("sff").filter((m) => m.guest === bob.id && m.kind === "confirmation").length, 2);
  assert.equal((await post(bob.id, "not-duplicate")).status, 409, "bob was never flagged");

  let r = await post(dupe.id, "not-duplicate");
  assert.equal(r.status, 200);
  assert.equal((await r.json()).dup.state, "cleared");
  assert.equal((await post(dupe.id, "not-duplicate")).status, 409);

  r = await post(dupe.id, "merge", { into: "sff:1" });
  assert.equal(r.status, 200);
  const m = await r.json();
  assert.deepEqual([m.merged.state, m.merged.mergedInto, m.merged.by, m.into.id, m.into.state, m.into.name], ["merged", "sff:1", "staff@yyc.hk", "sff:1", "accepted", "Jing HUANG"]);
  assert.equal((await post(dupe.id, "merge", { into: "sff:1" })).status, 409);
  assert.equal((await post(dupe.id, "accept")).status, 409);
  assert.equal((await post(dupe.id, "reject")).status, 409);
  assert.equal((await post("sff:2", "merge", { into: "sff:2" })).status, 400);
  assert.equal((await post("sff:2", "merge", {})).status, 400);
  assert.equal((await post("sff:2", "merge", { into: "nope" })).status, 404);
  assert.equal((await post("sff:2", "merge", { into: dupe.id })).status, 409, "not into a merged entry");
  mkEvent("other");
  await putGuests([{ id: "1", name: "X" }], "other");
  assert.equal((await post("sff:2", "merge", { into: "other:1" })).status, 400);

  r = await post("sff:2", "reject");
  assert.equal((await r.json()).state, "rejected");
  assert.equal(store.outbox("sff").filter((m) => m.guest === "sff:2").length, 0, "no message on reject");
  assert.equal((await post("sff:2", "accept")).status, 200, "a rejection can be undone");
  assert.equal((await post("nope", "accept")).status, 404);
  assert.equal((await post("sff:2", "frobnicate")).status, 404);
  assert.equal((await call(`/api/guests/sff:2/accept`, { method: "POST", token: await svcTok() })).status, 403);
});

test("staff actions: merging or rejecting an entry clears the flags that pointed at it", async () => {
  mkEvent();
  await submit("sff", { name: "Ann", email: "ann@example.com", phone: "61111111" });
  await submit("sff", { name: "Ann Again", email: "ann@example.com", phone: "62222222" });
  await settle();
  const [ann, again] = store.eventGuests("sff");
  assert.equal(again.dup.state, "flagged");
  store.act(ann.id, "merge", { by: "s", into: again.id }); // staff keep the second one
  assert.deepEqual(store.guest(again.id).dup, { state: "cleared", candidates: [] });
});

test("staff: the event's guests by state, the outbox newest first", async () => {
  mkEvent();
  await putGuests();
  await submit("sff", { name: "Bob", email: "b@example.com", phone: "62222222" });
  await settle();
  const t = await userTok();
  let r = await call("/api/events/sff/guests", { token: t });
  assert.equal((await r.json()).guests.length, 3);
  r = await call("/api/events/sff/guests?state=pending", { token: t });
  const [bob] = (await r.json()).guests;
  assert.equal(bob.name, "Bob");
  assert.equal((await call("/api/events/sff/guests?state=nope", { token: t })).status, 400);
  assert.equal((await call("/api/events/nope/guests", { token: t })).status, 404);
  assert.equal((await call("/api/events/sff/guests", { token: await svcTok() })).status, 403);

  store.act(bob.id, "accept", { by: "s" }, Date.now() - 60_000);
  store.act("sff:1", "reject", { by: "s" });
  store.act("sff:1", "accept", { by: "s" });
  const { messages } = await (await call("/api/events/sff/outbox", { token: t })).json();
  assert.deepEqual(messages.map((m) => m.guest), ["sff:1", "sff:1", bob.id, bob.id]);
  assert.deepEqual(Object.keys(messages[0]), ["id", "guest", "event", "kind", "channel", "to", "subject", "body", "status", "created"]);
});

test("staff-entered guest: accepted at once with a confirmation, the dup check still runs", async () => {
  mkEvent();
  await putGuests();
  llm = () => llmAnswer([{ id: "sff:2", reason: "姓名相同" }]);
  let r = await send("POST", "/api/events/sff/guests", { name: "Otto Wong", institution: "HKU", email: "", mobile: "" });
  assert.equal(r.status, 200);
  const g = await r.json();
  assert.deepEqual([g.source, g.state, g.by, g.dup.state, g.email, g.mobile], ["staff", "accepted", "staff@yyc.hk", "checking", null, null]);
  await settle();
  assert.deepEqual(store.guest(g.id).dup, { state: "flagged", candidates: [{ id: "sff:2", reason: "姓名相同" }] });
  assert.equal(store.guest(g.id).state, "accepted");
  assert.equal(store.outbox("sff").length, 0, "no address, no message");
  r = await send("POST", "/api/events/sff/guests", { name: "Walk In", email: "w@example.com", mobile: "+852 6000 0000" });
  assert.equal(store.outbox("sff").length, 2);
  assert.equal((await send("POST", "/api/events/sff/guests", { name: "", email: "bad" })).status, 400);
  assert.equal((await send("POST", "/api/events/nope/guests", { name: "A" })).status, 404);
  assert.equal((await send("POST", "/api/events/sff/guests", { name: "A" }, await svcTok())).status, 403);
  await settle();
});

// ---- check-in
test("checkin: an accepted code → {event, guest}; 404 unknown; 409 not accepted; service token allowed", async () => {
  mkEvent();
  await putGuests();
  const g = store.guest("sff:1");
  let r = await call(`/api/checkin?code=${g.code}`, { token: await userTok() });
  assert.equal(r.status, 200);
  const body = await r.json();
  assert.equal(body.event.id, "sff");
  assert.equal(body.guest.id, "sff:1");
  assert.equal((await call(`/api/checkin?code=${g.code}`, { token: await svcTok() })).status, 200);
  r = await call(`/api/checkin?code=${newCode()}`, { token: await userTok() });
  assert.deepEqual([r.status, await r.json()], [404, { error: "找不到此二維碼" }]);
  assert.equal((await call(`/api/checkin`, { token: await userTok() })).status, 404);
  const code = codeOf(await submit("sff", { name: "P", email: "p@example.com", phone: "61234567" }));
  await settle();
  r = await call(`/api/checkin?code=${code}`, { token: await userTok() });
  assert.deepEqual([r.status, await r.json()], [409, { error: "此報名未確認" }]);
  store.act("sff:1", "reject", { by: "s" });
  assert.equal((await call(`/api/checkin?code=${g.code}`, { token: await userTok() })).status, 409);
  assert.equal((await call(`/api/checkin?code=${g.code}`)).status, 403);
});

// ---- reminders (cron)
test("cron: a reminder for every accepted guest of tomorrow's events (Hong Kong date), once", async () => {
  // 2026-11-07T16:30Z is 2026-11-08 00:30 in Hong Kong: tomorrow there is 11-09 (in UTC it would be 11-08)
  const at = Date.parse("2026-11-07T16:30:00Z");
  mkEvent("tomorrow", { date: "2026-11-09" });
  mkEvent("utc-tomorrow", { date: "2026-11-08" });
  for (const e of ["tomorrow", "utc-tomorrow"]) await putGuests(guests, e);
  store.act("tomorrow:2", "reject", { by: "s" });
  assert.equal(store.reminders(at), 2, "one row per channel; guest 2 is rejected");
  const out = store.outbox("tomorrow");
  assert.deepEqual(out.map((m) => [m.guest, m.kind, m.channel, m.to, m.status]).sort(), [["tomorrow:1", "reminder", "email", "j@example.com", "off"], ["tomorrow:1", "reminder", "whatsapp", "+85291234567", "off"]]);
  assert.equal(store.outbox("utc-tomorrow").length, 0);
  assert.equal(store.reminders(at + 3600_000), 0, "the next hour writes nothing new");
  assert.ok(out[0].body.includes("2026-11-09"));
  assert.ok(out.find((m) => m.channel === "email").subject.includes("明天見"));
});

test("cron: the scheduled handler writes the reminders", async () => {
  mkEvent("tomorrow", { date: "2026-11-09" });
  await putGuests([guests[0]], "tomorrow");
  const waits = [];
  await worker.scheduled({ scheduledTime: Date.parse("2026-11-08T01:00:00Z"), cron: "0 * * * *" }, env, { waitUntil: (p) => waits.push(p) });
  await Promise.all(waits);
  assert.equal(store.outbox("tomorrow").length, 2);
});

// ---- rules, rendering
test("rules: phone and email keys, registration and event checks, HK dates", () => {
  assert.equal(phoneKey("9123 4567"), "85291234567");
  assert.equal(phoneKey("+852 9123-4567"), "85291234567");
  assert.equal(phoneKey("+44 7700 900123"), "447700900123");
  assert.equal(phoneKey(""), null);
  assert.equal(emailKey("  A@B.com "), "a@b.com");
  assert.deepEqual(checkRegistration({ name: "陳".repeat(100), email: "a@b.co", phone: "123456" }).errors, []);
  assert.equal(checkRegistration({ name: "陳".repeat(101), email: "a@b.co", phone: "123456" }).errors.length, 1);
  assert.equal(checkRegistration({ name: "A", email: "a@b.co", phone: "12345" }).errors.length, 1);
  assert.equal(checkRegistration({ name: "A", email: "a@b.co", phone: "1".repeat(21) }).errors.length, 1);
  assert.equal(checkRegistration({ name: "A", email: "a@b.co", phone: "6123 4567 ext 2" }).errors.length, 1);
  assert.deepEqual(checkRegistration({ name: "A" }, { required: false }).errors, []);
  assert.equal(checkEvent({ name: "x" }, { create: false }).name, "x");
  assert.equal(hkDate(Date.parse("2026-11-07T16:00:00Z")), "2026-11-08");
  assert.equal(hkDate(Date.parse("2026-11-07T15:59:59Z")), "2026-11-07");
  assert.equal(nextDay("2026-12-31"), "2027-01-01");
});

test("render: {{x}} escaped, {{{x}}} raw, one pass, missing → empty", () => {
  assert.equal(fill("<p>{{a}}</p>{{{b}}}{{c}}", { a: `<&"'>`, c: "{{a}}" }, { b: "<b>ok</b>" }), "<p>&lt;&amp;&quot;&#39;&gt;</p><b>ok</b>{{a}}");
  assert.equal(fill("[{{nope}}][{{{nope}}}]"), "[][]");
});

test("render: the QR SVG draws the encoder's modules with a quiet zone", () => {
  const text = `${REG}/r/${"A".repeat(22)}`;
  const qr = qrcodegen.QrCode.encodeText(text, qrcodegen.QrCode.Ecc.MEDIUM);
  const svg = qrSvg(text);
  const n = qr.size + 8;
  assert.ok(svg.startsWith(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${n} ${n}"`));
  const dark = (svg.match(/M\d+ \d+h1v1h-1z/g) || []).length;
  let count = 0;
  for (let y = 0; y < qr.size; y++) for (let x = 0; x < qr.size; x++) count += qr.getModule(x, y) ? 1 : 0;
  assert.equal(dark, count);
  assert.ok(svg.includes("M4 4h1v1h-1z"), "the finder pattern's corner sits inside the quiet zone");
  assert.equal(qr.version, 4, "58 bytes at ECC M fit version 4 (33×33)");
});

// ---- print flow
test("print → bridge next → done; status follows", async () => {
  await putGuests();
  const t = await userTok();
  let r = await call("/api/print?guest=sff:1", { method: "POST", token: t, headers: { "Content-Type": "image/png" }, body: png() });
  assert.equal(r.status, 200);
  const { id } = await r.json();
  assert.deepEqual(await (await call(`/api/print/${id}`, { token: t })).json(), { id, status: "queued", error: null });

  assert.equal((await call("/api/bridge/next", { token: t })).status, 403, "staff can't act as the bridge");
  r = await call("/api/bridge/next", { token: await svcTok() });
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("X-Job-Id"), id);
  assert.equal(r.headers.get("Content-Type"), "image/png");
  assert.deepEqual(new Uint8Array(await r.arrayBuffer()), png());
  assert.equal((await (await call(`/api/print/${id}`, { token: t })).json()).status, "printing");
  assert.equal((await call("/api/bridge/next", { token: await svcTok() })).status, 204);

  r = await call(`/api/bridge/jobs/${id}`, { method: "POST", token: await svcTok(), headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "done", error: null }) });
  assert.equal(r.status, 200);
  assert.deepEqual(await (await call(`/api/print/${id}`, { token: t })).json(), { id, status: "done", error: null });
});

test("jobs go out oldest first; failed carries the error", async () => {
  await putGuests();
  const t = await userTok(), s = await svcTok();
  const ids = [];
  for (const g of ["1", "2"]) ids.push((await (await call(`/api/print?guest=sff:${g}`, { method: "POST", token: t, headers: { "Content-Type": "image/png" }, body: png() })).json()).id);
  assert.equal((await call("/api/bridge/next", { token: s })).headers.get("X-Job-Id"), ids[0]);
  assert.equal((await call("/api/bridge/next", { token: s })).headers.get("X-Job-Id"), ids[1]);
  await call(`/api/bridge/jobs/${ids[1]}`, { method: "POST", token: s, body: JSON.stringify({ status: "failed", error: "碳帶已用完" }) });
  assert.deepEqual(await (await call(`/api/print/${ids[1]}`, { token: t })).json(), { id: ids[1], status: "failed", error: "碳帶已用完" });
  assert.equal((await call(`/api/bridge/jobs/${ids[1]}`, { method: "POST", token: s, body: JSON.stringify({ status: "weird" }) })).status, 400);
  assert.equal((await call(`/api/bridge/jobs/nope`, { method: "POST", token: s, body: JSON.stringify({ status: "done" }) })).status, 404);
});

test("print input checks", async () => {
  await putGuests();
  const t = await userTok();
  const post = (q, body, ct = "image/png") => call("/api/print" + q, { method: "POST", token: t, headers: { "Content-Type": ct }, body });
  assert.equal((await post("", png())).status, 400);
  assert.equal((await post("?guest=sff:1", png(), "application/octet-stream")).status, 415);
  assert.equal((await post("?guest=sff:1", new Uint8Array(100))).status, 400);
  assert.equal((await post("?guest=sff:1", png(850, 590))).status, 400);
  assert.equal((await post("?guest=sff:1", new Uint8Array(1 << 21))).status, 413);
  assert.equal((await post("?guest=sff:99", png())).status, 404);
  assert.equal((await call("/api/print/unknown", { token: t })).status, 404);
});

test("a job left printing too long is failed", async () => {
  await putGuests();
  const { id } = store.addJob({ id: "j1", byEmail: "a", guestId: "sff:1", png: png().buffer });
  const t0 = Date.now();
  store.next(null, t0);
  assert.equal(store.job(id, t0 + 1000).status, "printing");
  const j = store.job(id, t0 + PRINTING_TIMEOUT_MS + 1000);
  assert.equal(j.status, "failed");
  assert.ok(j.error);
});

// ---- printer status
test("printer: offline until the bridge polls; error is the latest decoded X-Printer-Error", async () => {
  const t = await userTok(), s = await svcTok();
  assert.deepEqual(await (await call("/api/printer", { token: t })).json(), { online: false, lastSeen: null, printer: "M3", error: null });
  await call("/api/bridge/next", { token: s, headers: { "X-Printer-Error": encodeURIComponent("碳帶已用完") } });
  let p = await (await call("/api/printer", { token: t })).json();
  assert.equal(p.online, true);
  assert.equal(p.error, "碳帶已用完");
  assert.match(p.lastSeen, /Z$/);
  await call("/api/bridge/next", { token: s });
  assert.equal((await (await call("/api/printer", { token: t })).json()).error, null);
  const seen = Date.parse((await (await call("/api/printer", { token: t })).json()).lastSeen);
  assert.equal(store.printer(seen + 10_000).online, true);
  assert.equal(store.printer(seen + 10_001).online, false);
});

test("printerError decodes percent-encoding and keeps undecodable text", () => {
  const r = (v) => new Request("https://x/", { headers: v == null ? {} : { "X-Printer-Error": v } });
  assert.equal(printerError(r(null)), null);
  assert.equal(printerError(r("%E7%A2%B3%E5%B8%B6%E5%B7%B2%E7%94%A8%E5%AE%8C")), "碳帶已用完");
  assert.equal(printerError(r("ribbon")), "ribbon");
  assert.equal(printerError(r("100%")), "100%");
});

// ---- app sign-in
test("app-signin: 302 to the app scheme with the CF_Authorization cookie", async () => {
  const t = await userTok();
  const state = "abcdefghij_KLMNOP-123";
  const r = await call(`/api/app-signin?state=${state}`, { token: t, headers: { Cookie: `other=1; CF_Authorization=${t}; x=y` } });
  assert.equal(r.status, 302);
  assert.equal(r.headers.get("Location"), `dev.de0ch.yycnametags://signed-in?state=${state}#token=${t}`);
  assert.equal(r.headers.get("Cache-Control"), "no-store");
});

test("app-signin: bad state, service token refused", async () => {
  const t = await userTok();
  for (const s of ["short", "x".repeat(129), "abcdefghijklmnop!", ""])
    assert.equal((await call(`/api/app-signin?state=${encodeURIComponent(s)}`, { token: t })).status, 400);
  assert.equal((await call(`/api/app-signin?state=${"a".repeat(16)}`, { token: await svcTok() })).status, 403);
});

test("app-signin: falls back to the assertion when there is no cookie", async () => {
  const t = await userTok();
  const r = await call(`/api/app-signin?state=${"a".repeat(16)}`, { token: t });
  assert.equal(r.headers.get("Location").split("#token=")[1], t);
});

test("pngSize", () => {
  assert.deepEqual(pngSize(png()), [848, 590]);
  assert.equal(pngSize(new Uint8Array(10)), null);
});

test("unknown paths are 404", async () => {
  assert.equal((await call("/api/nope", { token: await userTok() })).status, 404);
  assert.equal((await call("/api/bridge/nope", { token: await svcTok() })).status, 404);
});
