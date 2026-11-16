// The labels REST API, v2 (README.md "API"; OpenAPI at /api/v2/openapi.json). Labels arrive finished: bitmaps in the
// printer's own format, sent to a print page where staff print them. Templates are what labels are drawn from; changing
// one needs its lock, held by a person.
//
//   POST   /api/v2/prints?title=&template=&density=     body: 1-500 labels (application/octet-stream) → a print page
//   GET    /api/v2/prints                                the latest print requests
//   GET    /api/v2/prints/<id>                           one: status waiting | printing | printed | cancelled
//   GET    /api/v2/prints/<id>/labels/<n>.bin            one label (n from 1)
//   POST   /api/v2/prints/<id>/status                    {status?: "cancelled", printed?: n} (the print page)
//   GET    /api/v2/templates                             every template
//   GET    /api/v2/templates/<id>                        one: name, version, lock, files
//   GET    /api/v2/templates/<id>/files/<path>           one of its files
//   PUT    /api/v2/templates/<id>                        multipart: name, design.json, design.md, examples/1-4.bin, files/<name>…
//   DELETE /api/v2/templates/<id>
//   POST   /api/v2/templates/<id>/lock                   take the lock (409 locked: who holds it, since when)
//   POST   /api/v2/templates/<id>/lock/override          take it over from whoever holds it
//   DELETE /api/v2/templates/<id>/lock                   release it
//   GET    /api/v2/fonts                                 the fonts; GET /api/v2/fonts/<file> one (Content-Encoding: br)
import { LABEL_BYTES, MAX_FILE, MAX_LABELS, TEMPLATE_ID, checkFile } from "./store.js";
import { OPENAPI } from "./openapi.js";

const J = (v, status = 200, headers = {}) => Response.json(v, { status, headers: { "Cache-Control": "no-store", ...headers } });
const fail = (status, error, extra = {}) => J({ error, ...extra }, status);
const bin = (b) => new Response(b, { headers: { "Content-Type": "application/octet-stream", "Cache-Control": "no-store" } });
const PART = 100; // labels per call to the Store (6.3 MB)

const TYPES = { json: "application/json", md: "text/markdown; charset=utf-8", csv: "text/csv; charset=utf-8", png: "image/png",
  jpg: "image/jpeg", jpeg: "image/jpeg", svg: "image/svg+xml", py: "text/x-python; charset=utf-8", txt: "text/plain; charset=utf-8" };
const typeOf = (p) => TYPES[p.slice(p.lastIndexOf(".") + 1).toLowerCase()] || "application/octet-stream";

// The old API's answer (Deyao, 2026-11-04): a conversation that still uses it reads the skill again, or starts anew.
export const V1_GONE = "This API was replaced. Read the labels skill again and follow it: labels are now sent as finished " +
  "bitmaps to /api/v2. If the skill you read still describes /api/v1, ask the user to start a new conversation.";

const refusal = (r) => {
  const { status, error, ...rest } = r;
  const message = {
    locked: `${r.holder} is changing this template (since ${r.since}). Tell the user; they can ask ${r.holder} to release it, or take it over.`,
    not_locked: "Take the template's lock first (POST …/lock).",
    lock_taken_over: `${r.takenBy} took this template over at ${r.at}; the change was not saved.`,
  }[error];
  return fail(status || 400, error, { ...rest, ...(message ? { message } : {}) });
};

function printLink(o, id) { return `${o.origin}/p/${id}`; }

async function newPrint(req, o, url) {
  if (!o.who) return fail(400, "no_person", { message: "This request carries no person: labels are sent on someone's behalf." });
  const body = new Uint8Array(await req.arrayBuffer());
  const n = body.byteLength / LABEL_BYTES;
  if (!body.byteLength || !Number.isInteger(n))
    return fail(400, "bad_size", { message: `The body must be whole labels of ${LABEL_BYTES} bytes each (848 × 590 dots, 1 bit per dot); it is ${body.byteLength} bytes.` });
  if (n > MAX_LABELS) return fail(413, "too_many", { message: `At most ${MAX_LABELS} labels in one request; send the rest in another.` });
  const density = Number(url.searchParams.get("density") || 1);
  if (!Number.isInteger(density) || density < 1 || density > 5) return fail(400, "bad_density", { message: "density is 1 to 5" });
  const template = url.searchParams.get("template") || null;
  if (template && !TEMPLATE_ID.test(template)) return fail(400, "bad_template");
  const title = (url.searchParams.get("title") || "").slice(0, 120) || null;
  const id = crypto.randomUUID().replace(/-/g, "").slice(0, 16);
  await o.store.startPrint(id, { title, template, density, labels: n }, o.who);
  for (let k = 0; k < n; k += PART) {
    const parts = [];
    for (let i = k; i < Math.min(n, k + PART); i++) parts.push(body.slice(i * LABEL_BYTES, (i + 1) * LABEL_BYTES).buffer);
    await o.store.addLabels(id, k + 1, parts);
  }
  const p = await o.store.finishPrint(id);
  if (p.error) return fail(500, p.error);
  return J({ ...p, page: printLink(o, id) }, 201);
}

async function putTemplate(req, o, id) {
  let form;
  try { form = await req.formData(); } catch { return fail(400, "multipart", { message: "Send the template as multipart/form-data (curl -F)." }); }
  const name = String(form.get("name") || "").trim();
  if (!name) return fail(400, "no_name", { message: "Give the template a name (-F name=…): staff see it on the templates page." });
  const files = [];
  for (const [path, v] of form.entries()) {
    if (path === "name") continue;
    const bad = checkFile(path);
    if (bad) return fail(400, "bad_file", { message: `${path}: ${bad}` });
    const data = typeof v === "string" ? new TextEncoder().encode(v).buffer : await v.arrayBuffer();
    if (data.byteLength > MAX_FILE) return fail(413, "too_large", { message: `${path} is ${data.byteLength} bytes; at most ${MAX_FILE}` });
    files.push({ path, data });
  }
  const has = (p) => files.some((f) => f.path === p);
  const missing = ["design.json", "design.md", "examples/1.bin", "examples/2.bin", "examples/3.bin", "examples/4.bin"].filter((p) => !has(p));
  if (missing.length) return fail(400, "missing", { message: `A template needs ${missing.join(", ")}.` });
  for (const f of files.filter((f) => f.path.startsWith("examples/")))
    if (f.data.byteLength !== LABEL_BYTES) return fail(400, "bad_example", { message: `${f.path} must be one label of ${LABEL_BYTES} bytes.` });
  try { JSON.parse(new TextDecoder().decode(files.find((f) => f.path === "design.json").data)); }
  catch (e) { return fail(400, "bad_json", { message: `design.json is not JSON: ${e.message}` }); }
  const r = await o.store.putTemplate(id, name.slice(0, 80), files, o.who);
  return r.error ? refusal(r) : J({ ...r.template, page: `${o.origin}/` });
}

export async function handleApi(req, o, path) {
  const url = new URL(req.url);
  const m = req.method;
  const seg = path.slice("/api/v2/".length).split("/").map(decodeURIComponent);

  if (path === "/api/v2/openapi.json") return J(OPENAPI);

  if (seg[0] === "fonts") {
    if (seg.length === 1) {
      const r = await o.env.ASSETS.fetch(new Request(`${o.origin}/fonts/catalog.json`));
      const c = await r.json();
      return J({ fonts: c.fonts.map((f) => ({ family: f.family, label: f.label, category: f.category, weights: f.weights,
        files: f.files.map((x) => x.file) })) });
    }
    if (seg.length === 2 && /^[0-9a-f]{16}-[A-Za-z0-9.-]+\.(ttf|otf)$/.test(seg[1])) {
      const r = await o.env.ASSETS.fetch(new Request(`${o.origin}/fonts/${seg[1]}.br`));
      if (!r.ok) return fail(404, "no such font file");
      return new Response(r.body, { encodeBody: "manual", headers: { "Content-Type": "font/ttf", "Content-Encoding": "br", "Cache-Control": "private, max-age=86400" } });
    }
    return fail(404, "not found");
  }

  if (seg[0] === "prints") {
    if (seg.length === 1 && m === "POST") return newPrint(req, o, url);
    if (seg.length === 1 && m === "GET") return J((await o.store.prints()).map((p) => ({ ...p, page: printLink(o, p.id) })));
    const p = seg[1] && (await o.store.print(seg[1]));
    if (!p) return fail(404, "no such print request");
    if (seg.length === 2 && m === "GET") return J({ ...p, page: printLink(o, p.id) });
    if (seg.length === 4 && seg[2] === "labels" && m === "GET") {
      const n = Number((seg[3].match(/^(\d+)\.bin$/) || [])[1]);
      const b = n ? await o.store.label(p.id, n) : null;
      return b ? bin(b) : fail(404, "no such label");
    }
    if (seg.length === 3 && seg[2] === "status" && m === "POST") {
      let b;
      try { b = await req.json(); } catch { return fail(400, "the body must be JSON"); }
      const r = await o.store.setPrint(p.id, { status: b.status, printed: b.printed }, o.who || o.caller);
      return r.error ? fail(r.status, r.error) : J(r);
    }
    return fail(405, "method not allowed");
  }

  if (seg[0] === "templates") {
    if (seg.length === 1 && m === "GET") return J(await o.store.templates());
    const id = seg[1];
    if (!TEMPLATE_ID.test(id || "")) return fail(400, "bad_id", { message: "a template id is lowercase letters, digits and -, at most 40" });
    const changes = m !== "GET";
    if (changes && !o.who) return fail(400, "no_person", { message: "Changing a template needs a person: this request carries none." });
    if (seg.length === 2) {
      if (m === "GET") { const t = await o.store.template(id); return t ? J(t) : fail(404, "no such template"); }
      if (m === "PUT") return putTemplate(req, o, id);
      if (m === "DELETE") { const r = await o.store.deleteTemplate(id, o.who); return r.error ? refusal(r) : J(r); }
    }
    if (seg[2] === "files" && seg.length > 3 && m === "GET") {
      const fp = seg.slice(3).join("/");
      const b = await o.store.file(id, fp);
      return b ? new Response(b, { headers: { "Content-Type": typeOf(fp), "Cache-Control": "no-store" } }) : fail(404, "no such file");
    }
    if (seg[2] === "lock") {
      let r;
      if (seg.length === 3 && m === "POST") r = await o.store.lock(id, o.who);
      else if (seg.length === 4 && seg[3] === "override" && m === "POST") r = await o.store.override(id, o.who);
      else if (seg.length === 3 && m === "DELETE") r = await o.store.unlock(id, o.who);
      else return fail(405, "method not allowed");
      return r.error ? refusal(r) : J(r);
    }
    return fail(405, "method not allowed");
  }
  return fail(404, "not found");
}
