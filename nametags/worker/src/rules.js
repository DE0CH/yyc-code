// The registration rules that need no storage (nametags/README.md "Registration → QR → print"): form and event
// validation, the email/phone keys of the duplicate check, codes and ids, Hong Kong dates, and the outbox texts.

export const REGISTER_ORIGIN = "https://register.yyc-internal.com";
export const EVENT_ID_RE = /^[a-z0-9-]{3,40}$/;
const DATE_RE = /^\d{4}-\d\d-\d\d$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE_STRIP_RE = /[+\-() ]/g;

const len = (s) => [...s].length; // code points, so 陳大文 is 3
const trim = (v) => (v == null ? "" : String(v).trim());

// QR content of a guest's code
export const codeUrl = (code, origin = REGISTER_ORIGIN) => `${origin}/r/${code}`;
export const formUrl = (event, origin = REGISTER_ORIGIN) => `${origin}/e/${event}`;

// n random bytes as base64url without padding (16 bytes → the 22-char QR code)
export function randomId(n) {
  const b = crypto.getRandomValues(new Uint8Array(n));
  return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
export const newCode = () => randomId(16);
export const newGuestId = () => randomId(9);

// duplicate-check keys: email lower-case and trimmed; phone digits only, HK numbers (8 digits) with 852 in front
export const emailKey = (v) => trim(v).toLowerCase() || null;
export function phoneKey(v) {
  const d = trim(v).replace(/\D/g, "");
  if (!d) return null;
  return d.length === 8 ? "852" + d : d;
}
// WhatsApp address of a stored mobile: +<country><number>
export const phoneTo = (v) => (phoneKey(v) ? "+" + phoneKey(v) : null);

// public form (and staff entry with required = false for email/phone) → {values, errors}; errors are zh-HK / English lines
export function checkRegistration(input, { required = true } = {}) {
  const values = { name: trim(input.name), institution: trim(input.institution), email: trim(input.email), phone: trim(input.phone) };
  const errors = [];
  if (!values.name) errors.push("請填寫姓名 / Please enter your name");
  else if (len(values.name) > 100) errors.push("姓名不可超過 100 字 / Name must be at most 100 characters");
  if (len(values.institution) > 200) errors.push("機構名稱不可超過 200 字 / Institution must be at most 200 characters");
  if (!values.email) { if (required) errors.push("請填寫電郵 / Please enter your email"); }
  else if (values.email.length > 254 || !EMAIL_RE.test(values.email)) errors.push("電郵格式不正確 / Please enter a valid email address");
  if (!values.phone) { if (required) errors.push("請填寫電話 / Please enter your phone number"); }
  else {
    const d = values.phone.replace(PHONE_STRIP_RE, "");
    if (!/^\d{6,20}$/.test(d)) errors.push("電話須為 6 至 20 位數字 / Phone must be 6 to 20 digits");
  }
  return { values, errors };
}

const validDate = (s) => DATE_RE.test(s) && new Date(s + "T00:00:00Z").toISOString().slice(0, 10) === s;
const optText = (v, max, what) => {
  if (v == null || v === "") return null;
  if (typeof v !== "string" || len(v.trim()) > max) return Error(`${what} must be text of at most ${max} characters`);
  return v.trim() || null;
};

// POST/PATCH /api/events body → the fields to store, or a string saying what is wrong
export function checkEvent(body, { create }) {
  if (!body || typeof body !== "object" || Array.isArray(body)) return "body must be a JSON object";
  const out = {};
  if (create) {
    if (typeof body.id !== "string" || !EVENT_ID_RE.test(body.id)) return "id must be 3-40 of a-z 0-9 -";
    out.id = body.id;
  } else if ("id" in body) return "id cannot be changed";
  if (create || "name" in body) {
    if (typeof body.name !== "string" || !body.name.trim() || len(body.name.trim()) > 200) return "name must be 1-200 characters";
    out.name = body.name.trim();
  }
  for (const [k, max] of [["nameEn", 200], ["venue", 300]]) {
    if (!(k in body)) { if (create) out[k] = null; continue; }
    const v = optText(body[k], max, k);
    if (v instanceof Error) return v.message;
    out[k] = v;
  }
  if (create || "date" in body) {
    if (typeof body.date !== "string" || !validDate(body.date)) return "date must be YYYY-MM-DD";
    out.date = body.date;
  }
  if (create || "mode" in body) {
    if (body.mode !== "auto" && body.mode !== "review") return "mode must be auto or review";
    out.mode = body.mode;
  }
  if (create || "open" in body) {
    if (typeof body.open !== "boolean") return "open must be true or false";
    out.open = body.open;
  }
  if (!create && !Object.keys(out).length) return "nothing to change";
  return out;
}

// YYYY-MM-DD in Hong Kong at ms, and the day after
export const hkDate = (ms) => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Hong_Kong", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
export function nextDay(date) {
  const d = new Date(date + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}

// the outbox texts (zh-HK first, then English); one per channel, the email one with a subject
export function message(kind, { guest, event, origin = REGISTER_ORIGIN }) {
  const url = codeUrl(guest.code, origin);
  const en = event.nameEn || event.name;
  const where = event.venue ? `\n地點：${event.venue}` : "";
  const whereEn = event.venue ? `\nVenue: ${event.venue}` : "";
  const zh = kind === "confirmation"
    ? `${guest.name} 您好：\n\n您已成功報名「${event.name}」。\n日期：${event.date}${where}\n入場時請出示此二維碼：${url}`
    : `${guest.name} 您好：\n\n提提您，「${event.name}」將於明天（${event.date}）舉行。${where}\n入場時請出示此二維碼：${url}`;
  const eng = kind === "confirmation"
    ? `Dear ${guest.name},\n\nYour registration for ${en} is confirmed.\nDate: ${event.date}${whereEn}\nPlease show this QR code at the entrance: ${url}`
    : `Dear ${guest.name},\n\nA reminder that ${en} is tomorrow (${event.date}).${whereEn}\nPlease show this QR code at the entrance: ${url}`;
  const subject = kind === "confirmation" ? `報名確認 Registration confirmed: ${event.name}` : `明天見 See you tomorrow: ${event.name}`;
  return { subject, body: `${zh}\n\n${eng}` };
}
