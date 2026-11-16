// Durable Object Store (README.md "Storage"): the templates (each a set of files: design.json, design.md, four example
// labels, the template's own files), each template's lock, the print requests and their labels, and the live channel
// the templates page listens on. All in one SQLite database.
import { DurableObject } from "cloudflare:workers";

export const LABEL_BYTES = 62_540;          // 848 × 590 dots, 1 bit each
export const MAX_FILE = 1_500_000;          // bytes; a SQLite row in a Durable Object holds at most 2 MB
export const MAX_LABELS = 500;              // labels in one print request
export const TEMPLATE_ID = /^[a-z0-9][a-z0-9-]{0,39}$/;

// A template's files: design.json, design.md, examples/1-4.bin, files/<name> (anything else the template needs)
export function checkFile(path) {
  if (typeof path !== "string" || path.length > 200 || /[\u0000-\u001f\\]|\/\/|(^|\/)\.\.?(\/|$)/.test(path)) return "bad file name";
  if (path === "design.json" || path === "design.md" || /^examples\/[1-4]\.bin$/.test(path)) return null;
  if (/^files\/[^/][^\u0000]{0,120}$/.test(path)) return null;
  return "a template's files are design.json, design.md, examples/1.bin to examples/4.bin and files/<name>";
}

const now = () => new Date().toISOString();

export class Store extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    ctx.blockConcurrencyWhile(async () => this.#schema());
  }

  // 2026-11-04: the API's v2. Designs rendered by the Worker (designs/, lists/, logos/ in `files`) became templates
  // the AI draws from; the old rows go (the templates were carried over by hand, labels/README.md "History").
  #schema() {
    this.sql.exec(`DROP TABLE IF EXISTS files`);
    this.sql.exec(`DROP TABLE IF EXISTS collab_ops`);
    this.sql.exec(`DROP TABLE IF EXISTS collab_designs`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, version INTEGER NOT NULL,
      updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS template_files (template TEXT NOT NULL, path TEXT NOT NULL, data BLOB NOT NULL,
      size INTEGER NOT NULL, PRIMARY KEY (template, path))`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS locks (template TEXT PRIMARY KEY, holder TEXT NOT NULL, since TEXT NOT NULL)`);
    // every lock taken over: who took it from whom, so the one it was taken from is told why their change is refused
    this.sql.exec(`CREATE TABLE IF NOT EXISTS lock_overrides (template TEXT NOT NULL, taken_by TEXT NOT NULL,
      taken_from TEXT NOT NULL, held_since TEXT NOT NULL, at TEXT NOT NULL)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS prints (id TEXT PRIMARY KEY, title TEXT, template TEXT, density INTEGER NOT NULL,
      labels INTEGER NOT NULL, status TEXT NOT NULL, printed INTEGER NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
      changed_at TEXT NOT NULL, changed_by TEXT NOT NULL)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS print_labels (print TEXT NOT NULL, n INTEGER NOT NULL, bits BLOB NOT NULL,
      PRIMARY KEY (print, n))`);
  }

  // ---- templates ----------------------------------------------------------------------------------------------------
  templates() {
    return this.sql.exec(`SELECT * FROM templates ORDER BY name, id`).toArray().map((t) => this.#describe(t));
  }

  template(id) {
    const t = this.sql.exec(`SELECT * FROM templates WHERE id = ?`, id).toArray()[0];
    return t ? this.#describe(t) : null;
  }

  #describe(t) {
    const files = this.sql.exec(`SELECT path, size FROM template_files WHERE template = ? ORDER BY path`, t.id).toArray();
    return { id: t.id, name: t.name, version: t.version, updatedAt: t.updated_at, updatedBy: t.updated_by,
      lock: this.lockOf(t.id), files: files.map((f) => ({ path: f.path, size: f.size })) };
  }

  file(id, path) {
    const r = this.sql.exec(`SELECT data FROM template_files WHERE template = ? AND path = ?`, id, path).toArray()[0];
    return r ? r.data : null;
  }

  // The whole template at once (its files replace the old ones), only by the lock's holder.
  putTemplate(id, name, files, by) {
    const refused = this.#mustHold(id, by);
    if (refused) return refused;
    const cur = this.sql.exec(`SELECT version FROM templates WHERE id = ?`, id).toArray()[0]?.version ?? 0;
    const at = now();
    this.ctx.storage.transactionSync(() => {
      this.sql.exec(`DELETE FROM template_files WHERE template = ?`, id);
      for (const f of files) this.sql.exec(`INSERT INTO template_files (template, path, data, size) VALUES (?, ?, ?, ?)`, id, f.path, f.data, f.data.byteLength);
      this.sql.exec(`INSERT INTO templates (id, name, version, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET name = excluded.name, version = excluded.version, updated_at = excluded.updated_at,
        updated_by = excluded.updated_by`, id, name, cur + 1, at, by);
    });
    this.#broadcast({ kind: "template", id });
    return { template: this.template(id) };
  }

  deleteTemplate(id, by) {
    const refused = this.#mustHold(id, by);
    if (refused) return refused;
    this.ctx.storage.transactionSync(() => {
      this.sql.exec(`DELETE FROM template_files WHERE template = ?`, id);
      this.sql.exec(`DELETE FROM templates WHERE id = ?`, id);
      this.sql.exec(`DELETE FROM locks WHERE template = ?`, id);
    });
    this.#broadcast({ kind: "template", id });
    return { deleted: id };
  }

  // ---- locks --------------------------------------------------------------------------------------------------------
  lockOf(id) {
    const l = this.sql.exec(`SELECT holder, since FROM locks WHERE template = ?`, id).toArray()[0];
    return l ? { holder: l.holder, since: l.since } : null;
  }

  lock(id, who) {
    const l = this.lockOf(id);
    if (l && l.holder !== who) return { status: 409, error: "locked", ...l };
    if (!l) this.sql.exec(`INSERT INTO locks (template, holder, since) VALUES (?, ?, ?)`, id, who, now());
    this.#broadcast({ kind: "lock", id });
    return { lock: this.lockOf(id) };
  }

  override(id, who) {
    const l = this.lockOf(id);
    const at = now();
    if (l && l.holder !== who) {
      this.sql.exec(`INSERT INTO lock_overrides (template, taken_by, taken_from, held_since, at) VALUES (?, ?, ?, ?, ?)`,
        id, who, l.holder, l.since, at);
    }
    this.sql.exec(`INSERT INTO locks (template, holder, since) VALUES (?, ?, ?) ON CONFLICT(template) DO UPDATE SET
      holder = excluded.holder, since = excluded.since`, id, who, l && l.holder === who ? l.since : at);
    this.#broadcast({ kind: "lock", id });
    return { lock: this.lockOf(id), ...(l && l.holder !== who ? { tookFrom: l } : {}) };
  }

  unlock(id, who) {
    const l = this.lockOf(id);
    if (!l) return { released: false };
    if (l.holder !== who) return this.#notHolder(id, who, l);
    this.sql.exec(`DELETE FROM locks WHERE template = ?`, id);
    this.#broadcast({ kind: "lock", id });
    return { released: true };
  }

  #mustHold(id, who) {
    const l = this.lockOf(id);
    if (l && l.holder === who) return null;
    if (!l) return { status: 409, error: "not_locked" };
    return this.#notHolder(id, who, l);
  }

  // Someone else holds the lock: taken over from this person (since they last held it), or simply held by another.
  #notHolder(id, who, l) {
    const t = this.sql.exec(`SELECT taken_by, at FROM lock_overrides WHERE template = ? AND taken_from = ? ORDER BY at DESC LIMIT 1`,
      id, who).toArray()[0];
    if (t && t.at >= l.since) return { status: 409, error: "lock_taken_over", holder: l.holder, since: l.since, takenBy: t.taken_by, at: t.at };
    return { status: 409, error: "locked", ...l };
  }

  // ---- print requests -------------------------------------------------------------------------------------------------
  // labels: an array of ArrayBuffers (a request's labels arrive in parts: a call to a Durable Object carries 32 MiB)
  startPrint(id, { title, template, density, labels }, by) {
    const at = now();
    this.sql.exec(`INSERT INTO prints (id, title, template, density, labels, status, printed, created_at, created_by, changed_at, changed_by)
      VALUES (?, ?, ?, ?, ?, 'sending', 0, ?, ?, ?, ?)`, id, title, template, density, labels, at, by, at, by);
    return { id };
  }

  addLabels(id, first, parts) {
    parts.forEach((bits, k) => this.sql.exec(`INSERT INTO print_labels (print, n, bits) VALUES (?, ?, ?)`, id, first + k, bits));
    return { added: parts.length };
  }

  finishPrint(id) {
    const p = this.sql.exec(`SELECT labels FROM prints WHERE id = ?`, id).toArray()[0];
    const n = this.sql.exec(`SELECT count(*) AS n FROM print_labels WHERE print = ?`, id).toArray()[0].n;
    if (!p || n !== p.labels) return { error: "the labels did not all arrive" };
    this.sql.exec(`UPDATE prints SET status = 'waiting' WHERE id = ?`, id);
    return this.print(id);
  }

  print(id) {
    const p = this.sql.exec(`SELECT * FROM prints WHERE id = ? AND status <> 'sending'`, id).toArray()[0];
    return p ? { id: p.id, title: p.title, template: p.template, density: p.density, labels: p.labels, status: p.status,
      printed: p.printed, createdAt: p.created_at, createdBy: p.created_by, changedAt: p.changed_at, changedBy: p.changed_by } : null;
  }

  prints(limit = 50) {
    return this.sql.exec(`SELECT id FROM prints WHERE status <> 'sending' ORDER BY created_at DESC LIMIT ?`, limit).toArray()
      .map((r) => this.print(r.id));
  }

  label(id, n) {
    return this.sql.exec(`SELECT bits FROM print_labels WHERE print = ? AND n = ?`, id, n).toArray()[0]?.bits ?? null;
  }

  // The page reports each label printed (count = how many from the start, in order) and the end: printed or cancelled.
  setPrint(id, { status, printed }, by) {
    const p = this.print(id);
    if (!p) return { status: 404, error: "no such print request" };
    if (p.status === "cancelled" && status !== "cancelled") return { status: 409, error: "this print request was cancelled" };
    const count = Math.max(p.printed, Math.min(p.labels, Number(printed ?? p.printed) || 0));
    const next = status === "cancelled" ? "cancelled" : count >= p.labels ? "printed" : count > 0 ? "printing" : p.status;
    this.sql.exec(`UPDATE prints SET status = ?, printed = ?, changed_at = ?, changed_by = ? WHERE id = ?`, next, count, now(), by, id);
    this.#broadcast({ kind: "print", id, status: next, printed: count });
    return this.print(id);
  }

  // ---- /api/live: the templates page redraws when a template or a lock changes, a print page when its request does ---------------------------------------
  async fetch(req) {
    if (req.headers.get("Upgrade") !== "websocket") return new Response("websocket only", { status: 426 });
    const [client, server] = Object.values(new WebSocketPair());
    this.ctx.acceptWebSocket(server, ["live"]);
    return new Response(null, { status: 101, webSocket: client });
  }

  webSocketMessage(ws, msg) { if (msg === "ping") ws.send("pong"); }
  webSocketClose(ws, code) { try { ws.close(code, "bye"); } catch { /* already closed */ } }

  #broadcast(ev) {
    const s = JSON.stringify(ev);
    for (const ws of this.ctx.getWebSockets("live")) { try { ws.send(s); } catch { /* gone */ } }
  }
}
