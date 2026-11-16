// Durable Object Store (SQLite storage): events, their registrations (guests), the outbox, the print jobs and the
// bridge heartbeat. One instance ("main") holds everything; the Worker calls it over RPC. Rules: nametags/README.md
// "Registration → QR → print".
import { DurableObject } from "cloudflare:workers";
import { emailKey, phoneKey, phoneTo, newCode, newGuestId, message, hkDate, nextDay } from "./rules.js";

export const ONLINE_MS = 10_000; // bridge polled within 10 s = online
export const PRINTING_TIMEOUT_MS = 120_000; // a job "printing" this long with no report is failed
const KEEP_JOBS_MS = 7 * 86400_000;
// the sheet's fields (the app's Guest type); kept as JSON in registrations.data, id aside
const FIELDS = ["id", "name", "institution", "salutation", "surname", "firstname", "title", "email", "mobile", "status", "table"];
const LIVE = `state NOT IN ('rejected', 'merged')`; // entries a duplicate can be of
// the event the guest list loaded on 2026-10-29 became (its confirmation mails say Nov 9, 2026)
export const FIRST_EVENT = { id: "surgical-futures-forum", name: "Surgical Futures Forum", nameEn: null, date: "2026-11-09", venue: null, mode: "review", open: false };

const iso = (ms) => (ms == null ? null : new Date(ms).toISOString());
const sheetData = (g) => JSON.stringify(Object.fromEntries(FIELDS.slice(1).map((f) => [f, g[f] ?? null])));

export class Store extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, name TEXT NOT NULL, name_en TEXT,
      date TEXT NOT NULL, venue TEXT, mode TEXT NOT NULL, open INTEGER NOT NULL, created INTEGER NOT NULL)`);
    // pos = the sheet's row order for imported guests (null for the others); dup = {state, candidates} as JSON
    this.sql.exec(`CREATE TABLE IF NOT EXISTS registrations (id TEXT PRIMARY KEY, event TEXT NOT NULL, pos INTEGER,
      source TEXT NOT NULL, state TEXT NOT NULL, merged_into TEXT, created INTEGER NOT NULL, code TEXT UNIQUE NOT NULL,
      email_key TEXT, phone_key TEXT, dup TEXT NOT NULL, by_email TEXT, at INTEGER, data TEXT NOT NULL)`);
    this.sql.exec(`CREATE INDEX IF NOT EXISTS registrations_event ON registrations (event, state)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, guest TEXT NOT NULL, event TEXT NOT NULL,
      kind TEXT NOT NULL, channel TEXT NOT NULL, to_addr TEXT NOT NULL, subject TEXT, body TEXT NOT NULL,
      status TEXT NOT NULL, created INTEGER NOT NULL, UNIQUE (guest, kind, channel))`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, created INTEGER NOT NULL, by_email TEXT,
      guest_id TEXT, png BLOB, status TEXT NOT NULL, error TEXT, started INTEGER, finished INTEGER)`);
    this.sql.exec(`CREATE INDEX IF NOT EXISTS jobs_queue ON jobs (status, created)`);
    if (this.meta("schema") !== "2") ctx.storage.transactionSync(() => this.migrate());
  }

  // schema 1 had one guest list in `guests (pos, id, data)`: it becomes event FIRST_EVENT, ids `<event>:<sheet id>`
  migrate(now = Date.now()) {
    this.sql.exec(`CREATE TABLE IF NOT EXISTS guests (pos INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, data TEXT NOT NULL)`);
    const old = this.sql.exec(`SELECT pos, data FROM guests ORDER BY pos`).toArray();
    if (old.length) {
      if (!this.event(FIRST_EVENT.id)) this.createEvent(FIRST_EVENT, now);
      for (const r of old) this.insertImported(FIRST_EVENT.id, JSON.parse(r.data), r.pos, now);
      this.setMeta(`updated:${FIRST_EVENT.id}`, this.meta("guests_updated") ?? iso(now));
    }
    this.sql.exec(`DROP TABLE guests`);
    this.sql.exec(`DELETE FROM meta WHERE k = 'guests_updated'`);
    this.setMeta("schema", "2");
  }

  meta(k) { return this.sql.exec(`SELECT v FROM meta WHERE k = ?`, k).toArray()[0]?.v ?? null; }
  setMeta(k, v) { this.sql.exec(`INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v`, k, v); }
  touch(event, now) { this.setMeta(`updated:${event}`, iso(now)); }

  // ---- events
  toEvent(r) {
    return { id: r.id, name: r.name, nameEn: r.name_en ?? null, date: r.date, venue: r.venue ?? null, mode: r.mode,
      open: !!r.open, created: iso(r.created) };
  }

  event(id) {
    const r = this.sql.exec(`SELECT * FROM events WHERE id = ?`, id).toArray()[0];
    return r ? this.toEvent(r) : null;
  }

  // newest date first, each with its counts (flagged = an open duplicate flag on a pending or accepted entry)
  events() {
    return this.sql.exec(`SELECT * FROM events ORDER BY date DESC, id`).toArray().map((r) => {
      const c = this.sql.exec(`SELECT
          SUM(state = 'pending') AS pending, SUM(state = 'accepted') AS accepted, SUM(state = 'rejected') AS rejected,
          SUM(state IN ('pending', 'accepted') AND json_extract(dup, '$.state') = 'flagged') AS flagged
        FROM registrations WHERE event = ?`, r.id).toArray()[0];
      const outbox = this.sql.exec(`SELECT COUNT(*) AS n FROM outbox WHERE event = ?`, r.id).toArray()[0].n;
      return { ...this.toEvent(r), counts: { pending: c.pending ?? 0, flagged: c.flagged ?? 0, accepted: c.accepted ?? 0, rejected: c.rejected ?? 0, outbox } };
    });
  }

  // → the Event, or null when the id is taken
  createEvent(e, now = Date.now()) {
    if (this.event(e.id)) return null;
    this.sql.exec(`INSERT INTO events (id, name, name_en, date, venue, mode, open, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      e.id, e.name, e.nameEn ?? null, e.date, e.venue ?? null, e.mode, e.open ? 1 : 0, now);
    return this.event(e.id);
  }

  // → the Event, or null when there is none
  updateEvent(id, f) {
    const e = this.event(id);
    if (!e) return null;
    const n = { ...e, ...f };
    this.sql.exec(`UPDATE events SET name = ?, name_en = ?, date = ?, venue = ?, mode = ?, open = ? WHERE id = ?`,
      n.name, n.nameEn, n.date, n.venue, n.mode, n.open ? 1 : 0, id);
    return this.event(id);
  }

  // the app's default: the soonest event dated today or later (Hong Kong), else the latest
  defaultEvent(now = Date.now()) {
    const r = this.sql.exec(`SELECT * FROM events WHERE date >= ? ORDER BY date, id LIMIT 1`, hkDate(now)).toArray()[0]
      ?? this.sql.exec(`SELECT * FROM events ORDER BY date DESC, id LIMIT 1`).toArray()[0];
    return r ? this.toEvent(r) : null;
  }

  // ---- guests (registrations)
  toGuest(r) {
    const d = JSON.parse(r.data);
    const g = { id: r.id };
    for (const f of FIELDS.slice(1)) g[f] = d[f] ?? null;
    return { ...g, event: r.event, source: r.source, state: r.state, mergedInto: r.merged_into ?? null,
      created: iso(r.created), code: r.code, dup: JSON.parse(r.dup), by: r.by_email ?? null, at: iso(r.at) };
  }

  row(id) { return this.sql.exec(`SELECT * FROM registrations WHERE id = ?`, id).toArray()[0] ?? null; }
  guest(id) { const r = this.row(id); return r ? this.toGuest(r) : null; }
  hasGuest(id) { return !!this.row(id); }
  rows(event, where = "1", ...b) {
    return this.sql.exec(`SELECT * FROM registrations WHERE event = ? AND ${where}
      ORDER BY pos IS NULL, pos, created, rowid`, event, ...b).toArray();
  }

  // the app's list: accepted guests of the event (the default one without an id); event null when there is none
  guests(eventId = null, now = Date.now()) {
    const event = eventId ? this.event(eventId) : this.defaultEvent(now);
    if (!event) return { guests: [], updated: null, event: null };
    return { guests: this.rows(event.id, `state = 'accepted'`).map((r) => this.toGuest(r)), updated: this.meta(`updated:${event.id}`), event };
  }

  eventGuests(event, state = null) {
    return (state ? this.rows(event, `state = ?`, state) : this.rows(event)).map((r) => this.toGuest(r));
  }

  // → {guest, event} for a QR code, or null
  byCode(code) {
    const r = this.sql.exec(`SELECT * FROM registrations WHERE code = ?`, code).toArray()[0];
    return r ? { guest: this.toGuest(r), event: this.event(r.event) } : null;
  }

  insertImported(event, g, pos, now) {
    this.sql.exec(`INSERT INTO registrations (id, event, pos, source, state, created, code, email_key, phone_key, dup, data)
      VALUES (?, ?, ?, 'import', 'accepted', ?, ?, ?, ?, ?, ?)`, `${event}:${g.id}`, event, pos, now, newCode(),
      emailKey(g.email), phoneKey(g.mobile), JSON.stringify({ state: "none", candidates: [] }), sheetData(g));
  }

  // PUT /api/admin/guests?event=: replaces the event's imported guests (sheet ids → `<event>:<id>`). A guest already
  // there keeps its code, state and history; only its sheet fields and order change. Form and staff entries stay.
  putGuests(event, list, now = Date.now()) {
    this.ctx.storage.transactionSync(() => {
      const ids = new Set(list.map((g) => `${event}:${g.id}`));
      for (const r of this.rows(event, `source = 'import'`)) if (!ids.has(r.id)) this.sql.exec(`DELETE FROM registrations WHERE id = ?`, r.id);
      list.forEach((g, i) => {
        const id = `${event}:${g.id}`;
        if (this.row(id)) this.sql.exec(`UPDATE registrations SET pos = ?, data = ?, email_key = ?, phone_key = ? WHERE id = ?`,
          i, sheetData(g), emailKey(g.email), phoneKey(g.mobile), id);
        else this.insertImported(event, g, i, now);
      });
      this.touch(event, now);
    });
    return { count: list.length, updated: iso(now) };
  }

  // a form or staff entry (values from checkRegistration). The deterministic duplicate check runs here: the same email
  // or phone as another live entry of the event flags it at once; dup.state is otherwise "checking" until finishDup.
  addGuest(event, { values, source, by = null }, now = Date.now()) {
    const ek = emailKey(values.email), pk = phoneKey(values.phone);
    const candidates = this.rows(event, `${LIVE} AND ((email_key IS NOT NULL AND email_key = ?) OR (phone_key IS NOT NULL AND phone_key = ?))`, ek, pk)
      .map((r) => ({ id: r.id, reason: ek && r.email_key === ek ? "相同電郵" : "相同電話" }));
    const id = newGuestId(), staff = source === "staff";
    const g = { name: values.name, institution: values.institution || null, email: values.email || null, mobile: values.phone || null };
    this.ctx.storage.transactionSync(() => {
      this.sql.exec(`INSERT INTO registrations (id, event, source, state, created, code, email_key, phone_key, dup, by_email, at, data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`, id, event, source, staff ? "accepted" : "pending", now, newCode(), ek, pk,
        JSON.stringify({ state: candidates.length ? "flagged" : "checking", candidates }), staff ? by : null, staff ? now : null, sheetData(g));
      if (staff) this.enqueue(id, "confirmation", now);
      this.touch(event, now);
    });
    return this.guest(id);
  }

  // what the LLM check sees: the entry and the event's other live entries
  dupInput(id) {
    const r = this.row(id);
    if (!r) return null;
    const pick = (x) => { const g = this.toGuest(x); return { id: g.id, name: g.name, institution: g.institution, email: g.email, phone: g.mobile }; };
    return { entry: pick(r), others: this.rows(r.event, `${LIVE} AND id != ?`, id).map(pick) };
  }

  // the LLM check's result ({candidates} or {error}): candidates join the deterministic ones; a staff "not a duplicate"
  // given meanwhile stands. Then an unflagged pending entry of an auto event is accepted (+ confirmation).
  finishDup(id, { candidates = [], error = null }, now = Date.now()) {
    const r = this.row(id);
    if (!r) return null;
    this.ctx.storage.transactionSync(() => {
      const dup = JSON.parse(r.dup);
      if (dup.state !== "cleared") {
        const live = new Set(this.rows(r.event, `${LIVE} AND id != ?`, id).map((x) => x.id));
        for (const c of candidates) if (live.has(c.id) && !dup.candidates.some((d) => d.id === c.id)) dup.candidates.push(c);
        dup.state = dup.candidates.length ? "flagged" : error ? "error" : "none";
        if (error) dup.error = String(error).slice(0, 200);
        this.sql.exec(`UPDATE registrations SET dup = ? WHERE id = ?`, JSON.stringify(dup), id);
      }
      if (r.state === "pending" && dup.state !== "flagged" && this.event(r.event)?.mode === "auto") {
        this.sql.exec(`UPDATE registrations SET state = 'accepted', by_email = 'auto', at = ? WHERE id = ?`, now, id);
        this.enqueue(id, "confirmation", now);
      }
      this.touch(r.event, now);
    });
    return this.guest(id);
  }

  // a staff action → {guest} | {merged, into} | {status, error}. accept: pending or rejected; reject: pending or
  // accepted; not-duplicate: a flagged or errored check; merge A into B: both pending or accepted, same event.
  act(id, action, { by, into = null }, now = Date.now()) {
    const r = this.row(id);
    if (!r) return { status: 404, error: "no such guest" };
    const no = (what) => ({ status: 409, error: `${what} does not apply to a ${r.state} guest` });
    const set = (sql, ...b) => this.sql.exec(`UPDATE registrations SET ${sql}, by_email = ?, at = ? WHERE id = ?`, ...b, by, now, id);
    let out;
    this.ctx.storage.transactionSync(() => {
      if (action === "accept") {
        if (r.state !== "pending" && r.state !== "rejected") return (out = no("accept"));
        set(`state = 'accepted'`);
        this.enqueue(id, "confirmation", now);
      } else if (action === "reject") {
        if (r.state !== "pending" && r.state !== "accepted") return (out = no("reject"));
        set(`state = 'rejected'`);
        this.prune(r.event, id);
      } else if (action === "not-duplicate") {
        const dup = JSON.parse(r.dup);
        if (r.state === "merged" || (dup.state !== "flagged" && dup.state !== "error")) return (out = { status: 409, error: `not-duplicate does not apply to a ${dup.state} check` });
        set(`dup = ?`, JSON.stringify({ ...dup, state: "cleared" }));
      } else if (action === "merge") {
        const b = into && this.row(into);
        if (!b) return (out = { status: 404, error: "no such guest to merge into" });
        if (b.id === id) return (out = { status: 400, error: "cannot merge a guest into itself" });
        if (b.event !== r.event) return (out = { status: 400, error: "guests are in different events" });
        if (!["pending", "accepted"].includes(r.state)) return (out = no("merge"));
        if (!["pending", "accepted"].includes(b.state)) return (out = { status: 409, error: `cannot merge into a ${b.state} guest` });
        set(`state = 'merged', merged_into = ?`, b.id);
        this.prune(r.event, id);
      } else return (out = { status: 404, error: "no such action" });
      this.touch(r.event, now);
    });
    if (out) return out;
    return action === "merge" ? { merged: this.guest(id), into: this.guest(into) } : { guest: this.guest(id) };
  }

  // a rejected or merged entry is no longer anyone's duplicate: drop it from other flags; a flag left empty is cleared
  prune(event, gone) {
    for (const r of this.rows(event, `json_extract(dup, '$.state') = 'flagged' AND id != ?`, gone)) {
      const dup = JSON.parse(r.dup);
      const rest = dup.candidates.filter((c) => c.id !== gone);
      if (rest.length === dup.candidates.length) continue;
      this.sql.exec(`UPDATE registrations SET dup = ? WHERE id = ?`, JSON.stringify({ ...dup, candidates: rest, state: rest.length ? "flagged" : "cleared" }), r.id);
    }
  }

  // ---- outbox (sending is off: every message is written with status "off")
  // one row per channel the guest has an address for; a (guest, kind, channel) is written once
  enqueue(id, kind, now) {
    const g = this.guest(id), event = this.event(g.event);
    const { subject, body } = message(kind, { guest: g, event });
    const to = { email: g.email ? g.email.trim() : null, whatsapp: phoneTo(g.mobile) };
    for (const channel of ["email", "whatsapp"]) {
      if (!to[channel]) continue;
      this.sql.exec(`INSERT OR IGNORE INTO outbox (id, guest, event, kind, channel, to_addr, subject, body, status, created)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'off', ?)`, crypto.randomUUID(), id, g.event, kind, channel, to[channel],
        channel === "email" ? subject : null, body, now);
    }
  }

  outbox(event) {
    return this.sql.exec(`SELECT * FROM outbox WHERE event = ? ORDER BY created DESC, rowid DESC`, event).toArray().map((r) => ({
      id: r.id, guest: r.guest, event: r.event, kind: r.kind, channel: r.channel, to: r.to_addr, subject: r.subject ?? null,
      body: r.body, status: r.status, created: iso(r.created) }));
  }

  // the hourly cron: a reminder for every accepted guest of each event dated tomorrow in Hong Kong → rows written
  reminders(now = Date.now()) {
    const count = () => this.sql.exec(`SELECT COUNT(*) AS n FROM outbox`).toArray()[0].n;
    const before = count();
    const tomorrow = nextDay(hkDate(now));
    this.ctx.storage.transactionSync(() => {
      for (const e of this.sql.exec(`SELECT id FROM events WHERE date = ?`, tomorrow).toArray())
        for (const r of this.rows(e.id, `state = 'accepted'`)) this.enqueue(r.id, "reminder", now);
    });
    return count() - before;
  }

  // ---- print jobs
  addJob({ id, byEmail, guestId, png }, now = Date.now()) {
    this.sql.exec(`DELETE FROM jobs WHERE created < ?`, now - KEEP_JOBS_MS);
    this.sql.exec(`INSERT INTO jobs (id, created, by_email, guest_id, png, status) VALUES (?, ?, ?, ?, ?, 'queued')`,
      id, now, byEmail ?? null, guestId, png);
    return { id };
  }

  expireStuck(now) {
    this.sql.exec(`UPDATE jobs SET status = 'failed', error = '打印機沒有回應', finished = ?
      WHERE status = 'printing' AND started < ?`, now, now - PRINTING_TIMEOUT_MS);
  }

  job(id, now = Date.now()) {
    this.expireStuck(now);
    const r = this.sql.exec(`SELECT id, status, error FROM jobs WHERE id = ?`, id).toArray()[0];
    return r ? { id: r.id, status: r.status, error: r.error ?? null } : null;
  }

  // ---- bridge
  heartbeat(printerError, now = Date.now()) {
    this.setMeta("bridge_seen", String(now));
    this.setMeta("printer_error", printerError ?? "");
  }

  printer(now = Date.now()) {
    const seen = this.meta("bridge_seen");
    const lastSeen = seen ? Number(seen) : null;
    return {
      online: lastSeen != null && now - lastSeen <= ONLINE_MS,
      lastSeen: iso(lastSeen),
      printer: "M3",
      error: this.meta("printer_error") || null,
    };
  }

  // oldest queued job → marked printing; null when none
  next(printerError, now = Date.now()) {
    this.heartbeat(printerError, now);
    this.expireStuck(now);
    const r = this.sql.exec(`SELECT id, png FROM jobs WHERE status = 'queued' ORDER BY created, rowid LIMIT 1`).toArray()[0];
    if (!r) return null;
    this.sql.exec(`UPDATE jobs SET status = 'printing', started = ? WHERE id = ?`, now, r.id);
    const png = r.png instanceof ArrayBuffer ? r.png : new Uint8Array(r.png).slice().buffer;
    return { id: r.id, png };
  }

  // → the job's new state, or null when there is no such job
  report(id, { status, error }, printerError, now = Date.now()) {
    this.heartbeat(printerError, now);
    if (!this.sql.exec(`SELECT 1 FROM jobs WHERE id = ?`, id).toArray().length) return null;
    this.sql.exec(`UPDATE jobs SET status = ?, error = ?, finished = ?, png = NULL WHERE id = ?`,
      status, status === "failed" ? (error || "打印失敗") : null, now, id);
    return this.job(id, now);
  }
}
