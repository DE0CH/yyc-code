import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
import { blockPackets, jobStart, frame } from "../public/lib/print.js";
import worker, { personOf } from "../src/index.js";
import { V1_GONE } from "../src/api.js";
import { Store, checkFile, LABEL_BYTES } from "../src/store.js";
import { resetKeyCache } from "../../../nametags/worker/src/auth.js";

const pub = fileURLToPath(new URL("../public/", import.meta.url));
const hex = (u) => Buffer.from(u).toString("hex");

test("the M3 frames match niimbot.py byte for byte", () => {
  const bits = new Uint8Array(readFileSync(new URL("fixtures/label.bits", import.meta.url)));
  const ours = [...jobStart({ width: 848, height: 590 }, { density: 1, labelType: 1 }).map(([c, d]) => hex(frame(c, d))),
    ...blockPackets({ width: 848, height: 590, bits }).map(hex)];
  const theirs = readFileSync(new URL("fixtures/label.niimbot-py.hex", import.meta.url), "utf8").trim().split("\n");
  assert.deepEqual(ours, theirs);
});

test("the 0xa7 blocks carry the label's rows, whole, in order, each under 4096 bytes", () => {
  const bits = new Uint8Array(readFileSync(new URL("fixtures/label.bits", import.meta.url)));
  const blocks = blockPackets({ width: 848, height: 590, bits });
  const got = [];
  blocks.forEach((b, i) => {
    const n = (b[3] << 24) | (b[4] << 16) | (b[5] << 8) | b[6];
    assert.equal(b.length, n + 10);
    assert.deepEqual([b[7], b[8]], [i >> 8, i & 0xff], "block number");
    const lzo = b.subarray(9, 7 + n); // literal run: length code(s), the bytes, 11 00 00
    assert.deepEqual([...lzo.subarray(-3)], [0x11, 0, 0]);
    const body = lzo[0] ? lzo.subarray(1, -3) : lzo.subarray(lzo.findIndex((v, k) => k > 0 && v !== 0) + 1, -3);
    assert.ok(body.length <= 4096 && body.length % 106 === 0);
    got.push(...body);
  });
  assert.deepEqual(new Uint8Array(got), bits);
});

// ---- the Worker with a real Store on node:sqlite, the static assets from public/, and a stand-in for Access -------
function storeStub(db = new DatabaseSync(":memory:")) {
  const sockets = [];
  const ctx = {
    storage: { transactionSync: (f) => { db.exec("BEGIN"); try { const r = f(); db.exec("COMMIT"); return r; } catch (e) { db.exec("ROLLBACK"); throw e; } }, sql: { exec: (q, ...b) => { const st = db.prepare(q); const rows = /^\s*(SELECT|INSERT|CREATE|DELETE|UPDATE)/i.test(q) && /^\s*SELECT/i.test(q) ? st.all(...b.map(bind)) : (st.run(...b.map(bind)), []);
      return { toArray: () => rows.map((r) => ({ ...r, data: r.data instanceof Uint8Array ? r.data.buffer.slice(r.data.byteOffset, r.data.byteOffset + r.data.byteLength) : r.data })) }; } } },
    getWebSockets: () => sockets, acceptWebSocket: (ws) => sockets.push(ws), blockConcurrencyWhile: (f) => f(),
  };
  const s = new Store(ctx, {});
  return { s, sockets, db };
}
const bind = (v) => (v instanceof ArrayBuffer ? new Uint8Array(v) : v);

async function harness(extraEnv = {}) {
  resetKeyCache(); // each harness signs with a new key under the same kid
  const pair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  const jwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
  const b64u = (b) => Buffer.from(b).toString("base64url");
  const token = async (claims) => {
    const now = Math.floor(Date.now() / 1000);
    const head = b64u(JSON.stringify({ alg: "RS256", kid: "k" })), body = b64u(JSON.stringify({ iss: "https://t.cloudflareaccess.com", aud: ["aud"], exp: now + 60, ...claims }));
    return `${head}.${body}.${b64u(new Uint8Array(await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, Buffer.from(`${head}.${body}`))))}`;
  };
  const { s, sockets } = storeStub();
  const env = {
    ACCESS_TEAM: "t", ACCESS_AUD: "aud", ...extraEnv,
    STORE: { idFromName: () => "main", get: () => s },
    ASSETS: { fetch: async (r) => { const p = new URL(r.url ?? r).pathname; const f = pub + p.slice(1);
      return existsSync(f) ? new Response(readFileSync(f)) : new Response("no", { status: 404 }); } },
  };
  const fetcher = async () => Response.json({ keys: [{ ...jwk, kid: "k", alg: "RS256" }] });
  const call = async (path, { method = "GET", body, claims = { email: "a@yyc.hk" }, headers = {} } = {}) =>
    worker.fetch(new Request(`https://labels.example${path}`, { method, body, headers: { "Cf-Access-Jwt-Assertion": await token(claims), ...headers } }), env, {}, { fetcher });
  return { call, sockets, s };
}


test("access: no token or a bad one is refused", async () => {
  const { call } = await harness();
  const r = await worker.fetch(new Request("https://labels.example/api/v2/templates"), { ACCESS_TEAM: "t", ACCESS_AUD: "aud" }, {}, { fetcher: async () => Response.json({ keys: [] }) });
  assert.equal(r.status, 403);
  assert.equal((await call("/api/v2/templates", { claims: { email: "a@yyc.hk", aud: ["other"] } })).status, 403);
});

test("who a request acts for: staff by their sign-in; YYC AI by the person its egress names", () => {
  const req = (h) => new Request("https://x/", { headers: h });
  assert.equal(personOf({ kind: "user", email: "a@yyc.hk" }, req({ "X-Jarvis-Person": "b@yyc.hk" })), "a@yyc.hk");
  assert.equal(personOf({ kind: "service" }, req({ "Aviary-Sandbox-Person": " Jackie@YYC.hk " })), "jackie@yyc.hk");
  assert.equal(personOf({ kind: "service" }, req({ "X-Jarvis-Person": "b@yyc.hk" })), "b@yyc.hk");
  assert.equal(personOf({ kind: "service" }, req({})), null);
  assert.equal(personOf({ kind: "service" }, req({ "X-Jarvis-Person": "not an email" })), null);
});

test("v1 is gone: every call answers 410 with what to do", async () => {
  const { call } = await harness();
  for (const [m, p] of [["GET", "/api/v1/designs"], ["PUT", "/api/v1/designs/x"], ["POST", "/api/v1/files"]]) {
    const r = await call(p, { method: m, body: m === "GET" ? undefined : "{}", claims: { common_name: "svc" } });
    assert.equal(r.status, 410);
    assert.equal((await r.json()).message, V1_GONE);
  }
  assert.match(V1_GONE, /Read the labels skill again/);
  assert.match(V1_GONE, /start a new conversation/);
});

test("pages and assets", async () => {
  const { call } = await harness();
  assert.match(await (await call("/")).text(), /\/app\/page\.js/);
  assert.match(await (await call("/p/0123456789abcdef")).text(), /\/app\/page\.js/);
  assert.equal((await call("/d/forum")).status, 404);
  assert.match(await (await call("/lib/print.js")).text(), /export class Printer/);
  assert.equal((await call("/", { method: "POST" })).status, 405);
});

test("template files", () => {
  for (const ok of ["design.json", "design.md", "examples/1.bin", "examples/4.bin", "files/render.py", "files/logos/yyc.png", "files/名單.csv"]) assert.equal(checkFile(ok), null, ok);
  for (const bad of ["examples/5.bin", "x.txt", "files/../design.json", "files//x", "design.json/x"]) assert.ok(checkFile(bad), bad);
});

const label = (fill) => new Uint8Array(LABEL_BYTES).fill(fill);
const both = (...ls) => { const b = new Uint8Array(ls.length * LABEL_BYTES); ls.forEach((l, i) => b.set(l, i * LABEL_BYTES)); return b; };
const ai = (person) => ({ claims: { common_name: "svc" }, headers: person ? { "X-Jarvis-Person": person } : {} });

test("prints: sent by YYC AI for a person, shown and printed (or cancelled) on the page", async () => {
  const { call, sockets } = await harness();
  const heard = []; sockets.push({ send: (m) => heard.push(JSON.parse(m)) });
  let r = await call("/api/v2/prints", { method: "POST", body: both(label(0)), ...ai() });
  assert.equal(r.status, 400, "no person");
  r = await call("/api/v2/prints", { method: "POST", body: new Uint8Array(LABEL_BYTES + 1), ...ai("a@yyc.hk") });
  assert.equal((await r.json()).error, "bad_size");
  r = await call("/api/v2/prints?title=" + encodeURIComponent("論壇名牌") + "&template=forum&density=2", { method: "POST", body: both(label(0), label(255), label(1)), ...ai("a@yyc.hk") });
  assert.equal(r.status, 201);
  let p = await r.json();
  assert.deepEqual([p.labels, p.status, p.printed, p.title, p.template, p.density, p.createdBy], [3, "waiting", 0, "論壇名牌", "forum", 2, "a@yyc.hk"]);
  assert.equal(p.page, `https://labels.example/p/${p.id}`);
  const two = new Uint8Array(await (await call(`/api/v2/prints/${p.id}/labels/2.bin`)).arrayBuffer());
  assert.deepEqual([two.length, two[0], two[LABEL_BYTES - 1]], [LABEL_BYTES, 255, 255]);
  assert.equal((await call(`/api/v2/prints/${p.id}/labels/4.bin`)).status, 404);
  const status = async (body, who = {}) => (await call(`/api/v2/prints/${p.id}/status`, { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" }, ...who })).json();
  assert.deepEqual([(await status({ printed: 1 })).status, (await status({ printed: 3 })).status], ["printing", "printed"]);
  assert.equal((await status({ printed: 1 })).printed, 3, "progress never goes back");
  // another request, cancelled from the page
  p = await (await call("/api/v2/prints", { method: "POST", body: both(label(0)), ...ai("b@yyc.hk") })).json();
  const c = await status({ status: "cancelled" });
  assert.deepEqual([c.status, c.changedBy], ["cancelled", "a@yyc.hk"]);
  assert.equal((await call(`/api/v2/prints/${p.id}/status`, { method: "POST", body: JSON.stringify({ printed: 1 }) })).status, 409);
  assert.equal((await (await call(`/api/v2/prints/${p.id}`, ai("b@yyc.hk"))).json()).status, "cancelled", "YYC AI sees it");
  assert.deepEqual(heard.filter((e) => e.kind === "print").at(-1), { kind: "print", id: p.id, status: "cancelled", printed: 0 },
    "open print pages hear the cancel");
  const list = await (await call("/api/v2/prints")).json();
  assert.equal(list.length, 2);
});

test("prints: up to 500 labels arrive whole", async () => {
  const { call } = await harness();
  assert.equal((await call("/api/v2/prints", { method: "POST", body: new Uint8Array(LABEL_BYTES * 501), ...ai("a@yyc.hk") })).status, 413);
  const many = new Uint8Array(LABEL_BYTES * 250);
  many[LABEL_BYTES * 249] = 7;
  const p = await (await call("/api/v2/prints", { method: "POST", body: many, ...ai("a@yyc.hk") })).json();
  assert.equal(p.labels, 250);
  assert.equal(new Uint8Array(await (await call(`/api/v2/prints/${p.id}/labels/250.bin`)).arrayBuffer())[0], 7);
});

function templateForm(name = "論壇名牌", extra = {}) {
  const f = new FormData();
  if (name) f.append("name", name);
  f.append("design.json", new Blob([JSON.stringify({ templates: {} })]), "design.json");
  f.append("design.md", new Blob(["# 論壇名牌"]), "design.md");
  for (const n of [1, 2, 3, 4]) f.append(`examples/${n}.bin`, new Blob([label(n)]), `${n}.bin`);
  for (const [k, v] of Object.entries(extra)) f.append(k, new Blob([v]), k);
  return f;
}

test("templates: the lock, taken, refused with its holder, taken over, released", async () => {
  const { call } = await harness();
  const as = (person, method, path, body) => call(`/api/v2/templates/${path}`, { method, body, ...ai(person) });
  // a change needs the lock and a person
  assert.equal((await (await as("a@yyc.hk", "PUT", "forum", templateForm())).json()).error, "not_locked");
  assert.equal((await as(null, "POST", "forum/lock")).status, 400);
  let j = await (await as("a@yyc.hk", "POST", "forum/lock")).json();
  assert.equal(j.lock.holder, "a@yyc.hk");
  assert.equal((await as("a@yyc.hk", "POST", "forum/lock")).status, 200, "taking it again is fine");
  // checks on the template itself
  assert.equal((await (await as("a@yyc.hk", "PUT", "forum", templateForm(""))).json()).error, "no_name");
  const f = templateForm(); f.delete("examples/4.bin");
  assert.match((await (await as("a@yyc.hk", "PUT", "forum", f)).json()).message, /examples\/4\.bin/);
  const g = templateForm(); g.set("examples/2.bin", new Blob([new Uint8Array(10)]), "2.bin");
  assert.equal((await (await as("a@yyc.hk", "PUT", "forum", g)).json()).error, "bad_example");
  assert.equal((await (await as("a@yyc.hk", "PUT", "forum", templateForm("x", { "notes.txt": "x" }))).json()).error, "bad_file");
  // saved, with its files
  let r = await as("a@yyc.hk", "PUT", "forum", templateForm("論壇名牌", { "files/render.py": "print(1)" }));
  j = await r.json();
  assert.equal(r.status, 200);
  assert.deepEqual([j.name, j.version, j.updatedBy, j.files.map((x) => x.path)],
    ["論壇名牌", 1, "a@yyc.hk", ["design.json", "design.md", "examples/1.bin", "examples/2.bin", "examples/3.bin", "examples/4.bin", "files/render.py"]]);
  assert.equal(await (await call("/api/v2/templates/forum/files/files/render.py")).text(), "print(1)");
  assert.equal(new Uint8Array(await (await call("/api/v2/templates/forum/files/examples/3.bin")).arrayBuffer())[0], 3);
  // someone else: refused with who holds it
  r = await as("b@yyc.hk", "POST", "forum/lock");
  j = await r.json();
  assert.deepEqual([r.status, j.error, j.holder], [409, "locked", "a@yyc.hk"]);
  assert.ok(j.since);
  assert.equal((await (await as("b@yyc.hk", "PUT", "forum", templateForm())).json()).error, "locked");
  assert.equal((await (await as("b@yyc.hk", "DELETE", "forum/lock")).json()).error, "locked", "only the holder releases it");
  // taken over: the old holder's next change is refused, saying who took it and when
  j = await (await as("b@yyc.hk", "POST", "forum/lock/override")).json();
  assert.deepEqual([j.lock.holder, j.tookFrom.holder], ["b@yyc.hk", "a@yyc.hk"]);
  j = await (await as("a@yyc.hk", "PUT", "forum", templateForm())).json();
  assert.deepEqual([j.error, j.takenBy], ["lock_taken_over", "b@yyc.hk"]);
  assert.ok(j.at);
  j = await (await call("/api/v2/templates")).json();
  assert.deepEqual(j.map((t) => [t.id, t.lock.holder]), [["forum", "b@yyc.hk"]]);
  // b changes it and releases it; a takes it back the ordinary way
  assert.equal((await (await as("b@yyc.hk", "PUT", "forum", templateForm("論壇名牌（新）"))).json()).version, 2);
  assert.deepEqual(await (await as("b@yyc.hk", "DELETE", "forum/lock")).json(), { released: true });
  assert.equal((await (await as("a@yyc.hk", "POST", "forum/lock")).json()).lock.holder, "a@yyc.hk");
  assert.equal((await (await as("a@yyc.hk", "DELETE", "forum")).json()).deleted, "forum");
  assert.equal((await call("/api/v2/templates/forum")).status, 404);
  assert.equal((await as("a@yyc.hk", "GET", "Bad_Id")).status, 400);
});

const haveFonts = existsSync(new URL("../public/fonts/catalog.json", import.meta.url));
test("fonts and OpenAPI", { skip: !haveFonts && "no public/fonts" }, async () => {
  const { call } = await harness();
  const j = await (await call("/api/v2/fonts")).json();
  const hk = j.fonts.find((f) => f.family === "Noto Sans HK");
  assert.match(hk.files[0], /^[0-9a-f]{16}-NotoSansHK/);
  const r = await call(`/api/v2/fonts/${hk.files[0]}`);
  assert.equal(r.headers.get("content-encoding"), "br");
  assert.equal((await (await call("/api/v2/openapi.json")).json()).openapi, "3.1.0");
});

test("OpenAPI", async () => {
  const { call } = await harness();
  const j = await (await call("/api/v2/openapi.json")).json();
  assert.ok(j.paths["/api/v2/templates/{id}/lock/override"]);
});
