// The proof view's keyboard (Deyao, 2026-10-21: "i can use the proof page without using mouse, so flag and also notes
// should get keyboard shortcuts") and its notes kept in the browser (Deyao, 2026-10-21: "save notes locally and have one
// button and shortcut to download it as a file so i can process it later"): no database write, nothing sent anywhere.
// Pure functions, so tests/test_proof_keys.py checks them in Node.

export type KeyAction =
  | { a: "next" } | { a: "prev" } | { a: "nextSource" } | { a: "prevSource" }
  | { a: "flag" } | { a: "ok" } | { a: "stream"; n: number } | { a: "note" }
  | { a: "view" } | { a: "more" } | { a: "links" } | { a: "pageText"; dir: 1 | -1 } | { a: "fullscreen" }
  | { a: "download" } | { a: "clear" } | { a: "help" } | { a: "open" } | { a: "escape" }
  | { a: "noteSave" } | { a: "noteCancel" };

export interface KeyIn { key: string; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean; shiftKey?: boolean }

/** the keys, as the ? overlay and the hint line list them */
export const KEYS: [string, string][] = [
  ["→ or J", "next entry"], ["← or K", "previous entry"], ["] / [", "next / previous source"],
  ["X or !", "flag the entry as wrong (again: unflag)"], ["O", "mark the entry as correct"],
  ["1–9", "flag that stream's score as wrong (the number beside it)"],
  ["N", "write a note (Enter saves, Shift+Enter new line, Esc cancels)"],
  ["V", "In a stream / Passed stage 1 / Everything collected"], ["M", "show more / less of the text"], ["L", "all the links in the text / fewer"],
  ["Space / Shift+Space", "scroll the text"], ["Enter", "open the original"], ["F", "full screen"],
  ["D", "download the notes"], ["C", "clear the notes (asks first)"], ["?", "this list"], ["Esc", "close"],
];

/** a key pressed on the page → what it does, or null. typing: the focus is in a text field (only the note's save and
 *  cancel keys count then) */
export function keyAction(e: KeyIn, typing: boolean): KeyAction | null {
  const mod = !!(e.ctrlKey || e.metaKey || e.altKey);
  if (typing) {
    if (e.key === "Escape") return { a: "noteCancel" };
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey || !e.shiftKey)) return { a: "noteSave" };
    return null;
  }
  if (mod) return null;  // the browser's and the system's own shortcuts (Cmd+F, Cmd+D, Ctrl+R …)
  const k = e.key;
  if (k === "Escape") return { a: "escape" };
  if (k === "?") return { a: "help" };
  if (k === "!") return { a: "flag" };
  if (k === " " || k === "Spacebar") return { a: "pageText", dir: e.shiftKey ? -1 : 1 };
  if (e.shiftKey && k.length === 1 && /[a-z]/i.test(k)) return null;  // Shift+letter: nothing (keeps capitals free)
  switch (k) {
    case "ArrowRight": case "j": return { a: "next" };
    case "ArrowLeft": case "k": return { a: "prev" };
    case "]": return { a: "nextSource" };
    case "[": return { a: "prevSource" };
    case "x": return { a: "flag" };
    case "o": return { a: "ok" };
    case "n": return { a: "note" };
    case "v": return { a: "view" };
    case "m": return { a: "more" };
    case "l": return { a: "links" };
    case "f": return { a: "fullscreen" };
    case "d": return { a: "download" };
    case "c": return { a: "clear" };
    case "Enter": return { a: "open" };
  }
  if (/^[1-9]$/.test(k)) return { a: "stream", n: Number(k) };
  return null;
}

/** is this element a place where keys type text? */
export function isTyping(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || !el.closest) return false;
  if (el.closest("textarea, [contenteditable=''], [contenteditable='true']")) return true;
  const i = el.closest("input") as HTMLInputElement | null;
  return !!i && !/^(checkbox|radio|button|submit|range|color|file|reset)$/.test(i.type);
}

// ---- the notes, kept in this browser (localStorage) ----

export const NOTES_KEY = "yyc-proof-notes";

export interface ProofScore { stream: string; name: string; score: number | null; stage1: number | null; threshold: number | null; passed: boolean }
export interface ProofRecord {
  id: number; url: string; title: string | null; source: string | null; sid: string;
  published: string | null; seen: string | null;
  verdict: "wrong" | "ok" | null; wrong_streams: string[]; note: string;
  scores: ProofScore[];          // the scores the page showed when the record was last changed
  created: string; updated: string; downloaded: string | null;
}
export type Notes = Record<string, ProofRecord>;

export interface NotesStore { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void }

export function loadNotes(s: NotesStore | null): Notes {
  try { const v = s && s.getItem(NOTES_KEY); const o = v ? JSON.parse(v) : {}; return o && typeof o === "object" ? o : {}; }
  catch { return {}; }
}
/** → false when the browser refused (private mode, storage full): the page says the note lives only in this tab */
export function saveNotes(s: NotesStore | null, n: Notes): boolean {
  try { if (!s) return false; s.setItem(NOTES_KEY, JSON.stringify(n)); return true; } catch { return false; }
}

/** a record has something in it (a verdict, a stream flagged or a note); an emptied one is dropped */
export const hasContent = (r: ProofRecord) => !!(r.verdict || r.wrong_streams.length || r.note.trim());

export function upsert(n: Notes, base: Omit<ProofRecord, "verdict" | "wrong_streams" | "note" | "created" | "updated" | "downloaded">,
  change: (r: ProofRecord) => void, now: string): Notes {
  const old = n[base.id];
  const r: ProofRecord = old ? { ...old, ...base, wrong_streams: [...old.wrong_streams] }
    : { ...base, verdict: null, wrong_streams: [], note: "", created: now, updated: now, downloaded: null };
  change(r);
  r.updated = now;
  const out = { ...n };
  if (hasContent(r)) out[base.id] = r; else delete out[base.id];
  return out;
}

/** records changed since they were last downloaded (or never downloaded) */
export const pending = (n: Notes) => Object.values(n).filter(r => !r.downloaded || r.updated > r.downloaded).length;

/** the file: every record, oldest first, and the notes marked downloaded at `now` */
export function exportNotes(n: Notes, now: string): { name: string; body: string; notes: Notes } {
  const recs = Object.values(n).sort((a, b) => a.created.localeCompare(b.created) || a.id - b.id);
  const hk = new Date(Date.parse(now) + 8 * 3600e3).toISOString();  // the file name in Hong Kong time
  const name = `yyc-proof-notes-${hk.slice(0, 10)}-${hk.slice(11, 13)}${hk.slice(14, 16)}.json`;
  const body = JSON.stringify({ exported: now, page: "https://newswatch.yyc-internal.com/app/proof", count: recs.length,
    records: recs.map(({ downloaded: _d, ...r }) => r) }, null, 2);  // eslint-disable-line @typescript-eslint/no-unused-vars
  const notes: Notes = Object.fromEntries(recs.map(r => [r.id, { ...r, downloaded: now }]));
  return { name, body, notes };
}
