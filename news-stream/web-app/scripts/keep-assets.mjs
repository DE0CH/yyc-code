#!/usr/bin/env node
// Keeps the hashed files of earlier deploys in this deploy (README "Updates reach open pages"). A Worker deploy serves
// only the static files it uploads, so a tab still running the previous build would get 404 for a lazy file it has not
// loaded yet. Run after `vite build`, before `wrangler deploy` (the web-app workflow, `npm run deploy`):
//
//   node scripts/keep-assets.mjs [--site https://newswatch.yyc-internal.com] [--client dist/client] [--days 14]
//
// It reads the live site's /build-assets.json (written here by the previous deploy: every file under assets/ and the
// last time a live deploy had it), downloads each file of the last --days days that this build lacks into
// <client>/assets/, and writes <client>/build-assets.json for the next deploy. Hashed names never collide, and wrangler
// uploads only files Cloudflare does not hold yet. A site without the list (the first deploy) is crawled from its home
// page instead (the page's files and every assets/ path named in them). A site it cannot read is not an error: the
// deploy goes ahead with its own files only (a tab on the old build then reloads when a file is missing, lib/update.ts).
// The build id in the list is GITHUB_SHA's first 12 characters (vite.config.ts uses the same).
import { mkdir, readdir, writeFile } from "node:fs/promises";
import path from "node:path";

const arg = (name, dflt) => { const i = process.argv.indexOf("--" + name); return i > 0 ? process.argv[i + 1] : dflt; };
const SITE = arg("site", "https://newswatch.yyc-internal.com").replace(/\/+$/, "");
const CLIENT = path.resolve(arg("client", "dist/client"));
const DAYS = Number(arg("days", "14"));
const MAX_FILES = 5000;  // Workers static assets allow 20,000 files per version
const BUILD = (process.env.GITHUB_SHA || "").slice(0, 12) || null;

const log = (...a) => console.log("[keep-assets]", ...a);

async function get(url, timeoutMs = 20_000) {
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: ctl.signal, headers: { "User-Agent": "yyc-web-deploy" } });
    return { ok: r.ok, status: r.status, type: r.headers.get("content-type") || "", body: Buffer.from(await r.arrayBuffer()) };
  } finally { clearTimeout(t); }
}

async function own(dir, prefix = "assets") {
  const out = [];
  for (const e of await readdir(dir, { withFileTypes: true }).catch(() => [])) {
    const rel = prefix + "/" + e.name;
    if (e.isDirectory()) out.push(...await own(path.join(dir, e.name), rel)); else out.push(rel);
  }
  return out;
}

/** the first deploy: the site's files reachable from its home page */
async function crawl() {
  const seen = new Set(), queue = [];
  const add = text => { for (const m of text.matchAll(/assets\/[\w.-]+\.(?:js|css|woff2?|svg|png|webp)/g)) if (!seen.has(m[0])) { seen.add(m[0]); queue.push(m[0]); } };
  const home = await get(SITE + "/");
  if (!home.ok) throw new Error("home page " + home.status);
  add(home.body.toString("utf8"));
  const files = {};
  while (queue.length) {
    const p = queue.shift();
    const r = await get(SITE + "/" + p).catch(() => null);
    if (!r || !r.ok || r.type.startsWith("text/html")) continue;
    files[p] = r.body;
    if (/\.(js|css)$/.test(p)) add(r.body.toString("utf8"));
  }
  return files;
}

const now = new Date().toISOString();
const mine = await own(path.join(CLIENT, "assets"));
const mineSet = new Set(mine);
const next = Object.fromEntries(mine.map(p => [p, now]));
let kept = 0, missing = 0;

let listed = null;
try {
  const r = await get(SITE + "/build-assets.json");
  if (r.ok && r.type.includes("json")) listed = JSON.parse(r.body.toString("utf8")).files || null;
  else log(`no list on ${SITE} (${r.status}): crawling its home page`);
} catch (e) { log(`cannot read ${SITE}/build-assets.json: ${e.message}`); }

if (listed) {
  const cutoff = Date.now() - DAYS * 86400e3;
  const want = Object.entries(listed).filter(([p, last]) => !mineSet.has(p) && /^assets\/[\w./-]+$/.test(p) && !p.includes("..")
    && Date.parse(last) >= cutoff).sort((a, b) => b[1].localeCompare(a[1])).slice(0, Math.max(0, MAX_FILES - mine.length));
  for (let i = 0; i < want.length; i += 8) {
    await Promise.all(want.slice(i, i + 8).map(async ([p, last]) => {
      const r = await get(SITE + "/" + p).catch(() => null);
      if (!r || !r.ok || r.type.startsWith("text/html")) { missing++; return; }
      await mkdir(path.dirname(path.join(CLIENT, p)), { recursive: true });
      await writeFile(path.join(CLIENT, p), r.body);
      next[p] = last;
      kept++;
    }));
  }
} else {
  try {
    for (const [p, body] of Object.entries(await crawl())) {
      if (mineSet.has(p)) continue;
      await mkdir(path.dirname(path.join(CLIENT, p)), { recursive: true });
      await writeFile(path.join(CLIENT, p), body);
      next[p] = now;
      kept++;
    }
  } catch (e) { log(`cannot crawl ${SITE}: ${e.message}; deploying this build's files only`); }
}

await writeFile(path.join(CLIENT, "build-assets.json"), JSON.stringify({ build: BUILD, written: now, files: next }, null, 1) + "\n");
log(`${mine.length} files of this build, ${kept} kept from earlier deploys${missing ? `, ${missing} listed but gone` : ""}`);
