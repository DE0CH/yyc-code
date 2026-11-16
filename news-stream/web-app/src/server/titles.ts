// titles.py's display half: a post with a title from AI shows that title (written, or its own opening headline
// extracted, or the linked page's, or one replacing a title of its own that doesn't say what the post is about:
// retitled) with the post's text under it. Indexes are code points, as in Python.
const LEAD = 20;
const SKIP = /^(?:\s|[\p{So}\p{Sk}\p{Mn}\p{Cf}\p{Co}])$/u;

const skip = (ch: string) => SKIP.test(ch);

function squash(s: string): [string[], number[]] {
  const cs = Array.from(s || ""), out: string[] = [], idx: number[] = [];
  cs.forEach((ch, i) => { if (!skip(ch)) { out.push(ch); idx.push(i); } });
  return [out, idx];
}

function findIn(hay: string[], needle: string[]): number {
  if (!needle.length) return 0;
  outer: for (let i = 0; i + needle.length <= hay.length; i++) {
    for (let j = 0; j < needle.length; j++) if (hay[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

/** titles.find_headline → [start, end) in code points, or null */
export function findHeadline(text: string, headline: string): [number, number] | null {
  const [t, idx] = squash(text), [h] = squash(headline);
  if (!h.length || !t.length) return null;
  const k = findIn(t, h);
  if (k < 0 || k > LEAD || /[。！？!?.]/.test(t.slice(0, k).join(""))) return null;
  return [idx[k], idx[k + h.length - 1] + 1];
}

export function stripHeadline(text: string, headline: string): string {
  const at = findHeadline(text || "", headline);
  if (!at) return text;
  const rest = Array.from(text || "").slice(at[1]);
  let i = 0;
  while (i < rest.length && (skip(rest[i]) || "】」』]）):：｜|-–—".includes(rest[i]))) i++;
  return rest.slice(i).join("") || text;
}

export interface Shown {
  title: string; title_by_ai: boolean; title_extracted: boolean; title_linked: boolean; title_retitled: boolean;
  original_title: string | null; text: string;
}

export function shown(aiTitle: string, kind: string | null, title: string | null, text: string | null): Shown {
  const full = (text || "").trim() || title || "";
  const ext = kind === "extracted", lk = kind === "linked";
  return { title: aiTitle, title_by_ai: !(ext || lk), title_extracted: ext, title_linked: lk,
    title_retitled: kind === "retitled", original_title: title,
    text: ext ? stripHeadline(full, aiTitle) : full };
}

const TITLE_SLACK = 15;  // titles.TITLE_SLACK
const letters = (s: string | null | undefined) =>
  Array.from((s || "").replace(/https?:\/\/\S+/g, "").normalize("NFKC").toLowerCase()).filter(ch => /[\p{L}\p{N}]/u.test(ch)).join("");

/** titles.title_only: the text says nothing beyond a title (empty, a title again, a title plus links and at most
 *  TITLE_SLACK letters, or a part of a title) */
export function titleOnly(text: string | null | undefined, ...heads: (string | null | undefined)[]): boolean {
  const t = letters(text);
  if (!t) return true;
  return heads.some(h0 => { const h = letters(h0); return !!h && (h.includes(t) || (t.includes(h) && Array.from(t).length - Array.from(h).length <= TITLE_SLACK)); });
}

const WHOLE_TYPES = ["Social Media", "Forum"];  // titles.WHOLE_TYPES

/** titles.body: a shown post's text when it is more than its titles, else "" (the item page: no 全文) */
export const body = (d: Record<string, any>): string =>  // eslint-disable-line @typescript-eslint/no-explicit-any
  WHOLE_TYPES.includes(d.source_type) || d.thread ? (d.text || "")  // a social post's / comment's whole text, kept
    : titleOnly(d.text, d.title, d.original_title) ? "" : (d.text || "");

export type TextState = "full" | "due" | "whole" | "failed" | "none";

/** titles.text_state: what the item page's 內容 card says, from recorded state only (cached_at, text_due) */
export function textState(d: Record<string, any>, text: string, due: boolean): TextState {  // eslint-disable-line @typescript-eslint/no-explicit-any
  if (text.trim()) return "full";
  if (due) return "due";
  if ((WHOLE_TYPES.includes(d.source_type) && !(d.source_type === "Forum" && d.cached_at)) || d.thread) return "whole";
  return d.cached_at ? "failed" : "none";
}

/** titles.display: a post row as shown (mutates and returns it) */
export function display<T extends Record<string, any>>(d: T): T {  // eslint-disable-line @typescript-eslint/no-explicit-any
  if (d.ai_title) Object.assign(d, shown(d.ai_title, d.ai_title_kind ?? null, d.title ?? null, d.text ?? null));
  return d;
}

/** titles.item_display: a filed stream item as shown, from its post's current title from AI */
export function itemDisplay(it: Record<string, any>, aiTitle: string | null, kind: string | null, model: string | null,  // eslint-disable-line @typescript-eslint/no-explicit-any
  at: string | null, ptitle: string | null, ptext: string | null) {
  if (aiTitle) {
    Object.assign(it, shown(aiTitle, kind, ptitle || it.title || null, ptext || it.text || null));
    it.text = Array.from((it.text as string) || "").slice(0, 2000).join("") || null;
    it.ai_title_model = model; it.ai_title_at = at;
  }
  return it;
}

export const ITEM_JOIN = "LEFT JOIN posts p ON p.url = si.url";
export const ITEM_COLS = "p.ai_title, p.ai_title_kind, p.ai_title_model, p.ai_title_at, p.title AS p_title, p.text AS p_text";

/** corpus.shown_time: a date-only post's publish time is its bare day */
export function shownTime(p: { published?: string | null; date_only?: boolean | null }): string | null {
  const pub = p.published;
  if (pub && p.date_only) return pub.slice(0, 10);
  return pub ?? null;
}
