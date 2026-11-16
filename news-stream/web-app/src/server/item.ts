// The item page's data (item.py): everything the system knows about one post. Public: the headline, a short extract,
// the thread's comment extracts, tags, scoring, streams, coverage, article, clips and pictures; never the full text.
// Approved accounts (/app/item/text, /app/item/ai): the full text, comments and transcripts, and every stream's score.
import type { Row, Sql } from "./db";
import { config, pipelineOn, type EdgeConfig } from "./config";
import { kvGet, modelNames } from "./kv";
import { body, display, shownTime, textState } from "./titles";
import { dayAdd, isoMinutes, parseIso } from "./time";
import { dt, hkDayOf } from "../lib/dates";
import { grams, jaccard, topics } from "./topics";
import { video } from "./video";

export class NotFound extends Error {}

export async function find(sql: Sql, ref: { id?: number | string | null; url?: string | null }): Promise<number | null> {
  if (ref.id != null && String(ref.id).trim() !== "") {
    if (!/^\d+$/.test(String(ref.id).trim())) return null;
    const [r] = await sql`SELECT id FROM posts WHERE id = ${Number(ref.id)}`;
    return r ? (r.id as number) : null;
  }
  const url = (ref.url || "").trim();
  if (!url) return null;
  for (const u of [...new Set([url, url.replace(/#.*$/s, "")])]) {
    const [r] = await sql`SELECT id FROM posts WHERE url = ${u}`;
    if (r) return r.id as number;
  }
  return null;
}

async function row(sql: Sql, pid: number): Promise<Row | null> {
  const [r] = await sql`SELECT id, url, sid, source, website, board, author, platform, source_type, region, title, text,
      published, seen, day, engagement, cached_at, section, thread, article_id, date_only, ai_title, ai_title_kind,
      ai_title_model, ai_title_at, text_via, text_via_url, canonical_url FROM posts WHERE id = ${pid}`;
  if (!r) return null;
  const d: Row = { ...r };
  d.engagement = d.engagement ? JSON.parse(d.engagement) : {};
  return d;
}

/** Python's re.sub(r"\s+", " ", t).strip() then a cut at n code points */
function short(text: string | null, n: number): [string, boolean] {
  const t = (text || "").replace(/\s+/g, " ").trim();
  const cs = Array.from(t);
  return cs.length > n ? [cs.slice(0, n).join("") + "…", true] : [t, false];
}

const jsonList = (s: string | null) => (s ? JSON.parse(s) : []);

function comment(r: Row, n: number) {
  const [text, cut] = short(r.text, n);
  const eng = r.engagement ? JSON.parse(r.engagement) : {};
  return { id: r.id, author: r.author, time: r.published || r.seen, text, cut, likes: eng.likes ?? null, dislikes: eng.dislikes ?? null } as Row;
}

export async function thread(sql: Sql, c: EdgeConfig, x: Row, full = false) {
  const base: string = x.thread || x.url.replace(/#.*$/s, "");
  let parent: Row | null = null;
  if (x.thread) {
    const [p] = await sql`SELECT id, title, website, author, published, seen, ai_title, ai_title_kind FROM posts WHERE url = ${base}`;
    if (p) {
      const d = display({ ...p });
      parent = Object.fromEntries(Object.entries(d).filter(([k]) => !["ai_title", "ai_title_kind", "text"].includes(k)));
    }
  }
  const [rows, [sc]] = await Promise.all([
    sql`SELECT id, author, text, published, seen, engagement FROM posts WHERE thread = ${base}
        ORDER BY coalesce(published, seen), id`,
    sql`SELECT s.comments_upto, s.at FROM scored s JOIN posts p ON p.id = s.post_id WHERE p.url = ${base}`,
  ]);
  const upto = sc ? (sc.comments_upto as number | null) : null;
  const comments = rows.map(r => comment(r, full ? 10 ** 6 : c.item.comment_extract));
  if (upto != null) comments.forEach((cm, i) => { cm.scored = (rows[i].id as number) <= upto; });
  return { parent, total: rows.length, stated: !x.thread ? (x.engagement || {}).comments ?? null : null,
    comments, scored_at: upto != null ? sc.at : null };
}

const COV_COLS = "id, url, title, website, board, author, source_type, published, seen, day, date_only, ai_title, ai_title_kind";

/** the same story from other outlets (item.coverage): the post's topic among the posts within coverage_days days.
 *  Candidates must share a title bigram with the post (SQL prefilter on the shown title, normalised), then the
 *  similarity and topics() decide exactly as in Python */
export async function coverage(sql: Sql, c: EdgeConfig, x: Row) {
  if (x.thread) return [];
  const day: string = x.day, I = c.item;
  const g = [...grams(x.title)];
  const two = g.filter(b => Array.from(b).length === 2);
  // a title with no letter pair (empty, or one character) is compared with every post, as in Python
  const pre = two.length ? "AND lower(coalesce(ai_title, title, '')) LIKE ANY(ARRAY(SELECT jsonb_array_elements_text($4::jsonb)))" : "";
  const args: unknown[] = [dayAdd(day, -I.coverage_days), dayAdd(day, I.coverage_days), x.id];
  if (two.length) args.push(two.map(b => "%" + b.replace(/[\\%_]/g, "\\$&") + "%"));
  const rows = await sql.unsafe(`SELECT ${COV_COLS} FROM posts WHERE thread IS NULL AND day BETWEEN $1 AND $2 AND id <> $3 ${pre}`, args as never[]);
  const near: Row[] = [];
  for (const r0 of rows) {
    const r = display({ ...r0 });
    if (jaccard(g0(x.title), grams(r.title)) >= I.coverage_min) near.push(r);
  }
  const me = { id: x.id, url: x.url, title: x.title, website: x.website, board: x.board, author: x.author,
    source_type: x.source_type, published: x.published, seen: x.seen, day: x.day };
  for (const t of topics([me, ...near] as Row[] as never[])) {
    const ps = (t as { posts: Row[] }).posts;
    if (ps.some(p => p.id === x.id)) {
      const others = ps.filter(p => p.id !== x.id && (p.website || "") !== (x.website || ""));
      others.sort((a, b) => { const ka = a.published || a.seen || "", kb = b.published || b.seen || ""; return ka < kb ? -1 : ka > kb ? 1 : 0; });
      return others.slice(0, I.coverage_max).map(p => ({
        id: p.id, title: p.title, website: p.website ?? null, board: p.board ?? null, author: p.author ?? null,
        source_type: p.source_type ?? null, title_by_ai: p.title_by_ai ?? null, title_linked: p.title_linked ?? null,
        time: shownTime(p) || p.seen,
      }));
    }
  }
  return [];
}
const g0 = grams;

export async function tags(sql: Sql, c: EdgeConfig, x: Row) {
  const names = new Map(c.profiles.map(p => [p.id, p]));
  const rows = await sql`SELECT profile, relevant, sentiment, labels, theme, tagged_at, corrected, corrected_by,
      corrected_at FROM tags WHERE post_id = ${x.id}`;
  const out = [];
  for (const r of rows) {
    const p = names.get(r.profile);
    if (!p) continue;
    out.push({ profile: r.profile, name: p.name, title: p.title || p.name, kind: p.kind, relevant: !!r.relevant,
      sentiment: r.sentiment, labels: jsonList(r.labels), theme: r.theme, tagged_at: r.tagged_at,
      corrected: !!r.corrected, corrected_by: r.corrected_by, corrected_at: r.corrected_at });
  }
  const order = [...names.keys()];
  return out.sort((a, b) => order.indexOf(a.profile as string) - order.indexOf(b.profile as string));
}

export async function scoring(sql: Sql, c: EdgeConfig, x: Row) {
  const [r] = await sql.unsafe(`SELECT s.sentiment, s.labels, s.at, s.hash <> ${c.hash_sql} AS changed
      FROM scored s JOIN posts p ON p.id = s.post_id WHERE s.post_id = $1`, [x.id]);
  if (!r) return null;
  return { at: r.at, sentiment: r.sentiment, labels: jsonList(r.labels), rescoring: !!r.changed, second_due: await secondDue(sql, x) };
}

/** item.HKT_MIN: a TIMESTAMPTZ column as HKT ISO minutes */
const HKT_MIN = (col: string) => `to_char(${col} AT TIME ZONE 'Asia/Hong_Kong', 'YYYY-MM-DD"T"HH24:MI') || '+08:00'`;
const NOW_HKT = `(to_char(now() AT TIME ZONE 'Asia/Hong_Kong', 'YYYY-MM-DD"T"HH24:MI:SS') || '+08:00')`;

/** item.second_due: a unit post's second scoring (twopass.py) still to come → when it is due, else null */
async function secondDue(sql: Sql, x: Row): Promise<string | null> {
  const [r] = await sql.unsafe(`SELECT ${HKT_MIN("due_at")} AS due FROM two_pass
      WHERE url = $1 AND due_at IS NOT NULL AND second_at IS NULL`, [x.url]);
  return r ? (r.due as string) : null;
}

const WHY_ZH = (c: EdgeConfig, st: string, n: number, waits: string[], until: string | null) => {
  const base = `這則帖子除連結、#標籤、@提及和表情符號外只有 ${n} 個字（少於 ${c.thin.threshold} 個），也沒有影片逐字稿、圖片文字或有意義的圖片。`;
  if (st === "waiting") {
    const what = waits.map(w => c.thin.waits_zh[w] || w).join("、");
    return base + `正等待${what}，` + (until ? `最遲 ${dt.when(until)} ` : "") + "再判斷；未有內容不作串流評分。";
  }
  return base + "內容不足，不作串流評分，不會列入串流或發送 Telegram；之後若補回內容（例如逐字稿），會再評分。";
};

/** thin.status: the near-empty gate's verdict on the post's current content, or null */
export async function thinStatus(sql: Sql, c: EdgeConfig, pid: number) {
  const [r] = await sql.unsafe(`SELECT t.*, p.seen AS p_seen, ${c.hash_sql} AS now_hash FROM thin_posts t
      JOIN posts p ON p.id = t.post_id WHERE t.post_id = $1`, [pid]);
  if (!r || r.hash !== r.now_hash) return null;
  const waits: string[] = JSON.parse(r.waits || "[]");
  let until: string | null = null;
  if (r.status === "waiting") {
    const s = parseIso(r.p_seen);
    until = s ? isoMinutes({ ms: s.ms + c.thin.wait_s * 1000, offset: s.offset }) : null;
  }
  return { status: r.status, units: r.units, threshold: c.thin.threshold, waits, until, label: c.thin.label, at: r.at,
    why: WHY_ZH(c, r.status, r.units, waits, until) };
}

export async function notScored(sql: Sql, c: EdgeConfig, x: Row): Promise<string> {
  const s = await kvGet<{ from_id?: number; comments?: boolean }>(sql, "scoring", {});
  if (!s || !Object.keys(s).length || x.id <= Number(s.from_id || 0)) return "before";
  if (x.thread && !s.comments) return "comment";
  const when = parseIso(x.published || x.seen), seen = parseIso(x.seen);
  if (when && seen && seen.ms - when.ms > c.max_age_s * 1000) return "old";
  const t = await thinStatus(sql, c, x.id);
  if (t) return t.status === "rejected" ? "thin" : "thin_wait";
  return "due";
}

export async function article(sql: Sql, c: EdgeConfig, x: Row) {
  if (!x.article_id) return null;
  const rows = await sql`SELECT id, title, website, board, author, source_type, published, seen, date_only, ai_title,
      ai_title_kind FROM posts WHERE article_id = ${x.article_id} AND id <> ${x.id}
      ORDER BY coalesce(published, seen), id LIMIT ${c.item.article_max}`;
  if (!rows.length) return null;
  return { id: x.article_id, posts: rows.map(r0 => {
    const r = display({ ...r0 });
    return { id: r.id, title: r.title, website: r.website, board: r.board, author: r.author, source_type: r.source_type,
      title_by_ai: r.title_by_ai ?? null, title_linked: r.title_linked ?? null, time: shownTime(r) || r.seen };
  }) };
}

export async function media(sql: Sql, c: EdgeConfig, x: Row, full = false) {
  // queue: a queued clip's place in the transcription queue (item.QUEUE_POS, media.due's order), 1 = next
  const rows = await sql.unsafe(`SELECT m.key, m.url, m.host, m.kind, m.status, m.duration, m.model, m.error, m.done_at,
      m.transcript, m.tries, m.captions, m.captions_model, CASE WHEN m.status = 'queued' AND (m.next_try IS NULL OR m.next_try <= ${NOW_HKT}) THEN 1 + (
        SELECT count(*) FROM media q WHERE q.status = 'queued' AND (q.next_try IS NULL OR q.next_try <= ${NOW_HKT})
         AND (q.newest > m.newest OR (m.newest IS NULL AND q.newest IS NOT NULL)
              OR (q.newest IS NOT DISTINCT FROM m.newest AND q.key < m.key))) END AS queue
      FROM post_media pm JOIN media m ON m.key = pm.key WHERE pm.post_id = $1
      ORDER BY m.first_seen, m.key`, [x.id]);
  const name = await modelNames(sql);
  // transcription off (Deyao, 2026-10-20: cost): a turned-off feature is absent, never announced (NAVIGATION.md): a
  // queued clip is left out, or shown with only its YouTube subtitles (status captions)
  const talkOn = pipelineOn(c, "transcription");
  return rows.filter(r => talkOn || r.status !== "queued" || !!r.captions).map(r => {
    const model: string | null = r.model;
    const status = r.status === "queued" && !talkOn ? "captions" : r.status;
    const d: Row = { key: r.key, host: r.host, kind: r.kind, status,
      duration: r.duration != null ? pyRound(Number(r.duration)) : null, model,
      model_name: captionsLabel(model) ?? (model ? name(model) : null),
      // 「AI 轉錄」 only on our model's transcript; YouTube's subtitles (uploader's or auto-generated) are not
      done_at: r.done_at, by_ai: model ? !captionsLabel(model) : null,
      // a YouTube clip's own subtitles, kept beside the transcript (media.captions)
      captions_name: captionsLabel(r.captions_model), captions_chars: Array.from(r.captions || "").length,
      chars: Array.from(r.transcript || "").length, error: r.status === "failed" || r.status === "no_direct" ? Array.from(r.error || "").slice(0, 200).join("") : null,
      queue: r.queue == null || status !== "queued" ? null : Number(r.queue) };
    if (full) { d.transcript = r.transcript || ""; d.captions = r.captions || ""; }
    return d;
  });
}

/** Python's round(): halves to even */
function pyRound(v: number) {
  const f = Math.floor(v), d = v - f;
  return d > 0.5 ? f + 1 : d < 0.5 ? f : f % 2 === 0 ? f : f + 1;
}

export async function pictures(sql: Sql, c: EdgeConfig, x: Row, full = false) {
  const th = c.images.threshold;
  const picsOn = pipelineOn(c, "pictures");  // off (Deyao, 2026-10-20: cost)
  const rows = await sql`SELECT pi.url, pi.pos, pi.hash, pi.tries, i.score, i.reason, i.text, i.width, i.height, i.model
      FROM post_images pi LEFT JOIN images i ON i.hash = pi.hash WHERE pi.post_id = ${x.id} ORDER BY pi.pos, pi.url`;
  const out: Row[] = [], seen = new Set<string | null>();
  for (const r of rows) {
    if (r.hash && seen.has(r.hash)) continue;
    seen.add(r.hash);
    if (r.width != null && Math.min(r.width, r.height || 0) < c.images.min_side) continue;
    const st = r.score != null ? "scored" : r.tries >= c.images.tries ? "failed" : "queued";
    if (st !== "scored" && !picsOn) continue;  // picture analysis off: a picture not analysed is left out (absent)
    const d: Row = { url: r.url, hash: r.hash, status: st, score: r.score, meaningful: r.score != null && r.score >= th,
      reason: r.reason, width: r.width, height: r.height, model: r.model, chars: Array.from(r.text || "").length };
    if (full) d.text = r.text || "";
    out.push(d);
  }
  return { meaningful_from: th, items: out };
}

/** a transcript read from YouTube's subtitles (media.py CAPTIONS / AUTO_CAPTIONS; item.captions_label) → its label */
export function captionsLabel(model: string | null): string | null {
  for (const [prefix, word] of [["youtube-captions:", "YouTube 上載者字幕"], ["youtube-auto-captions:", "YouTube 自動字幕"]] as const)
    if ((model || "").startsWith(prefix)) return `${word}（${model!.slice(prefix.length)}）`;
  return null;
}

function viaLabel(c: EdgeConfig, via: string | null, name: (m: string) => string) {
  if ((via || "").startsWith("asr:")) return `AI 轉錄（${name(via!.slice(4))}）`;
  if (captionsLabel(via)) return captionsLabel(via);
  return c.via[via || ""] ?? null;
}

async function base(sql: Sql, pid: number) {
  const c = await config(sql);
  const x = await row(sql, pid);
  if (!x) throw new NotFound("no such post");
  const textDue = willRead(c, x);  // on the stored row (a title from AI moves the old title into the text)
  display(x);
  return { c, x, textDue };
}

const hostOf = (url: string) => { try { return new URL(url).hostname.toLowerCase(); } catch { return ""; } };

/** fulltext_fetch.will_read: the full-text job will read the post's page for its text (a news, official or blog post,
 *  or one of PAGE_SIDS, not a comment, never tried, no text or only a feed summary, on a host it reads), or a YouTube
 *  video post's description its collector didn't get (sources.youtube_due) */
export function willRead(c: EdgeConfig, x: Row): boolean {
  const f = c.fulltext;
  if (!f) return false;
  // seen before the restart cutoff (corpus.SCORE_FROM_SEEN): ignored, never read
  if (f.from_seen && !(x.seen && Date.parse(String(x.seen)) >= Date.parse(f.from_seen))) return false;
  if (f.youtube_sid && f.youtube_url && x.cached_at == null && !x.thread && !String(x.text || "").trim() &&
      new RegExp(f.youtube_sid).test(String(x.sid || "")) && new RegExp(f.youtube_url).test(x.url || "")) return true;
  const h = hostOf(x.url || "");
  // fulltext_fetch.forum_page: a Forum-type post that is no thread (twopass.UNIT_URLS = comment_urls) is read too
  const forumPage = x.source_type === "Forum" && !(c.comment_urls || []).some(u => new RegExp(u).test(x.url || ""));
  return x.cached_at == null && !x.thread && (f.types.includes(x.source_type) || (f.sids || []).includes(String(x.sid || "")) || forumPage) &&
    Array.from(((x.text as string | null) || "").trim()).length < f.full_chars && !String(x.sid || "").startsWith("tikhub_") &&
    !f.skip_hosts.some(s => h.endsWith(s));  // cache_fetch: host(url).endswith(SKIP_HOSTS)
}

/** a post whose comments a fetcher reads (twopass.UNIT_URLS, POSIX regexes that read the same in JavaScript) */
const commentsRead = (c: EdgeConfig, x: Row) => !x.thread && (c.comment_urls || []).some(u => new RegExp(u).test(x.url));

const LINK_RE = /https?:\/\/[^\s<>"'）)」』，。、；！？\]]+/g;  // = item.py LINK_RE (follow.URL_RE)
const LINK_TRAIL = ".,;:!?…'\"”’";
const LINKS_HIDDEN = ["share", "not_web", "media", "home"];  // follow.skip_reason's buttons, pictures, front pages

/** item.links: the post's links; as link following resolved them (follow.py: the post reached a stream: the real
 *  address, the outlet, our own copy's id), else the links in its text as they stand */
async function links(sql: Sql, x: Row) {
  if (x.thread) return [];
  const [rows, scanned] = await Promise.all([
    sql`SELECT f.url, f.final_url, f.host, f.status, f.reason, f.target_id, p.website FROM follow_links f
        LEFT JOIN posts p ON p.id = f.target_id WHERE f.post_id = ${x.id} ORDER BY f.id`,
    sql`SELECT 1 AS one FROM follow_scan WHERE post_id = ${x.id}`,
  ]);
  if (scanned.length)
    return rows.filter(r => !(r.status === "skipped" && LINKS_HIDDEN.includes(r.reason)))
      .map(r => ({ url: r.final_url || r.url, host: r.host, name: r.website ?? null, item_id: r.target_id ?? null, status: r.status }));
  const out: Row[] = [];
  for (const m of `${x.text || ""}\n${x.title || ""}`.matchAll(LINK_RE)) {
    let u = m[0];
    while (u && LINK_TRAIL.includes(u[u.length - 1])) u = u.slice(0, -1);
    if (u !== x.url && !out.some(o => o.url === u))
      out.push({ url: u, host: (u.match(/^https?:\/\/([^/?#:]+)/)![1]).toLowerCase().replace(/^www\./, ""), name: null, item_id: null, status: null });
  }
  return out;
}

/** the public JSON of the item page, without coverage (itemCoverage: the page shows it when it comes) */
export async function view(sql: Sql, pid: number) {
  const { c, x, textDue } = await base(sql, pid);
  const text: string = body(x);  // only the title again is no text (titles.titleOnly)
  const ex = text ? short(text, c.item.extract)[0] : "";
  const [streams, t, th, sc, art, md, pics, name, lk, vid] = await Promise.all([
    // day: 串流's day, the notification's (feed.ts SENT_DAY)
    sql`SELECT DISTINCT stream, left(item->>'seen', 10) AS day, item->>'category' AS category FROM stream_items
      WHERE url = ${x.url} ORDER BY day, stream`,
    tags(sql, c, x), thread(sql, c, x), scoring(sql, c, x), article(sql, c, x), media(sql, c, x), pictures(sql, c, x),
    modelNames(sql), links(sql, x), video(sql, x.id),
  ]);
  const sname = new Map(c.streams.map(s => [s.id, s.name]));
  return {
    id: x.id, url: x.url, link: x.canonical_url || x.url, title: x.title, website: x.website, board: x.board,
    author: x.author, platform: x.platform, source_type: x.source_type, region: x.region, sid: x.sid, source: x.source, published: shownTime(x),
    date_only: !!x.date_only, seen: x.seen, day: x.day, cached_at: x.cached_at, comment: !!x.thread, thread_url: x.thread,
    title_by_ai: !!x.title_by_ai, title_extracted: !!x.title_extracted, title_linked: !!x.title_linked,
    title_retitled: !!x.title_retitled, original_title: x.original_title ?? null, ai_title_model: x.ai_title_model ?? null,
    ai_title_model_name: x.ai_title_model ? name(x.ai_title_model) : null, ai_title_at: x.ai_title_at ?? null,
    extract: ex, text_chars: Array.from(text.trim()).length, text_state: textState(x, text, textDue), engagement: x.engagement,
    text_via: x.text_via ?? null, text_via_label: viaLabel(c, x.text_via, name), text_via_url: x.text_via_url ?? null,
    text_due: textDue, comments_read: commentsRead(c, x),
    tags: t, matched: t.filter(r => r.relevant).map(r => r.profile),
    streams: streams.map(r => ({ stream: r.stream, name: sname.get(r.stream) ?? r.stream, day: r.day, category: r.category })),
    scoring: sc, not_scored: sc ? null : await notScored(sql, c, x), article: art,
    thin: sc ? null : await thinStatus(sql, c, x.id),
    thread: th, media: md, pictures: pics, links: lk, video: vid,
    vocab: { sentiments: c.sentiments, labels: c.label_order, label_info: c.label_info },
  };
}

export async function itemCoverage(sql: Sql, pid: number) {
  const { c, x } = await base(sql, pid);
  return coverage(sql, c, x);
}

/** approved accounts: the full stored text, the full comments, the transcripts and the text read from pictures */
export async function text(sql: Sql, pid: number) {
  const { c, x } = await base(sql, pid);
  const [th, md, pics] = await Promise.all([thread(sql, c, x, true), media(sql, c, x, true), pictures(sql, c, x, true)]);
  return { id: x.id, text: body(x), cached_at: x.cached_at,
    comments: Object.fromEntries(th.comments.map(cm => [cm.id, cm.text])),
    media: Object.fromEntries(md.filter(m => m.transcript).map(m => [m.key, m.transcript])),
    media_captions: Object.fromEntries(md.filter(m => m.captions).map(m => [m.key, m.captions])),
    pictures: Object.fromEntries(pics.items.filter(p => p.text).map(p => [p.hash, p.text])) };
}

/** naive times as the cluster's pods read them (datetime.astimezone on a naive value: the pod's zone, UTC) → epoch ms */
function podMs(v: string | Date | null | undefined): number | null {
  if (v == null) return null;
  if (v instanceof Date) return isNaN(+v) ? null : +v;
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(String(v).trim());
  const t = parseIso(String(v).trim() + (hasZone || !/T|\s\d/.test(String(v)) ? "" : "+00:00"));
  return t ? t.ms : null;
}

/** item.rescored: a filed item whose current score is under the threshold → one sentence, or null */
function rescored(c: EdgeConfig, k: Row | undefined, score: number | null, th: number, at: string | null, passes: Row[],
  sentCur: Record<string, number>): string | null {
  if (!k || (score != null && score >= th)) return null;
  const filedScore = k.item.score ?? null, stream: string = k.stream;
  let first = "", filedAt = podMs(k.item.seen);
  for (const p of passes) {
    const got = (((p.result || {}).streams || {})[k.parent || stream]) || {};
    if (got.score === filedScore && filedScore != null) { filedAt = podMs(p.at); first = p.pass === 1 ? "首次" : ""; break; }
  }
  const now = podMs(at);
  const bothToday = filedAt != null && now != null && hkDayOf(filedAt) === hkDayOf(now);
  const fmt = (t: number | null) => (t == null ? "" : bothToday ? dt.time(t) : dt.dateTime(t));
  const chat = c.streams.find(s => s.id === stream)?.telegram;
  let sent: boolean | null = null;
  if (chat) { const cur = (sentCur || {})[stream]; sent = cur != null && k.id <= cur; }
  const did = sent ? "已收錄並發送" : "已收錄";
  const keep = sent ? "已發送的項目保留" : chat ? "項目保留在串流，並照常發送" : "已收錄的項目保留";
  const was = filedScore != null ? `${first}評分 ${filedScore} 分，${did}` : did;
  const nowS = score != null ? `重新評分為 ${score} 分（低於門檻 ${th}）` : `重新評分低於 ${c.floor} 分（門檻 ${th}）`;
  const when = (t: number | null, what: string) => `${fmt(t)} ${what}`.trim();
  return `${when(filedAt, was)}；${when(now, nowS)}，${keep}。`;
}

/** a stage-1 score under the floor: stored since 21 Oct 2026 (streams.py stores every stream's score), shown on the
 *  staff pages as before (「低於 20 分」, no row); a stage-2 score under it was stored before too and shows as it did */
export function hiddenLow(c: EdgeConfig, r: Row | undefined): boolean {
  return !!r && (r.score as number) < c.floor && (r.model == null || r.model === c.model);
}

/** approved accounts: the scorer's analysis of the post against every stream (item.ai_scores). exact (the proof
 *  view, Deyao only): every stored score, 0–19 too, and `stored` (false = no row: scored before every score was kept) */
export async function aiScores(sql: Sql, pid: number, exact = false) {
  const { c, x } = await base(sql, pid);
  const sc = await scoring(sql, c, x);
  const [srows, krows, mrows, name] = await Promise.all([
    sql`SELECT stream, score, reason, category, scored_at, model, screen, screen_reason FROM scores WHERE post_id = ${x.id}`,
    sql`SELECT id, stream, left(item->>'seen', 10) AS day, item FROM stream_items WHERE url = ${x.url} ORDER BY id`,
    sql`SELECT stream, market, how, note, model, at FROM markets WHERE post_id = ${x.id}`,
    modelNames(sql),
  ]);
  const passes = await sql`SELECT pass, at, result FROM score_passes WHERE post_id = ${x.id} ORDER BY pass`;
  const sentCur = await kvGet<Record<string, number>>(sql, "telegram_sent", {});
  const rows = new Map(srows.filter(r => exact || !hiddenLow(c, r)).map(r => [r.stream, r]));
  const parent = new Map(c.streams.filter(s => s.parent).map(s => [s.id, s.parent!]));
  const kept = new Map<string, Row>();
  for (const r of krows) {
    if (!kept.has(r.stream)) kept.set(r.stream, r);
    const p = parent.get(r.stream);
    if (p !== undefined && !kept.has(p)) kept.set(p, { ...r, parent: p });
  }
  const mk = new Map(mrows.map(r => [r.stream, { ...r }]));
  const out: Row[] = [];
  for (const st of c.streams.filter(s => c.scored.includes(s.id))) {
    const r = rows.get(st.id), k = kept.get(st.id);
    const th = c.thresholds[st.id];
    let score = r ? (r.score as number) : null;
    let reason = r ? r.reason : null, cat = r ? r.category : null;
    if (!r && k && k.item.score != null && !sc) { score = k.item.score; reason = k.item.reason ?? null; cat = k.item.category ?? null; }
    const o: Row = { stream: st.id, name: st.name, title: st.title ?? st.name, score, reason: reason || null, category: cat,
      scored_at: r ? r.scored_at : null, threshold: th,
      // two-stage scoring (streams.confirm): whose score this is, and the screening score stage 1 gave
      model: r ? name((r.model as string | null) || c.model) : null,
      screen: r && r.screen != null ? { score: r.screen as number, reason: (r.screen_reason as string | null) || null, model: name(c.model) } : null, passed: score != null && score >= th,
      kept: k ? { day: k.day, score: k.item.score ?? null } : null, member: !!k || (score != null && score >= th),
      rescored: rescored(c, k, score, th, (r ? r.scored_at : null) || (sc ? sc.at : null), passes as Row[], sentCur) };
    if (exact) o.stored = !!r;
    out.push(o);
    if (st.split) {
      // one child on (Galbot 香港 while the flag `mainland` is off; streams.sole_market): no classifier, every post goes
      // there; `how: "only"` tells the page to show no 市場 line (a turned-off feature is absent from the site)
      const on = c.streams.filter(ch => ch.parent === st.id && c.on[ch.id!]);
      if (on.length === 1) {
        o.market = { market: on[0].market ?? null, how: "only", note: null, model: null, at: null, stream: on[0].id, name: on[0].name };
        continue;
      }
      const m = mk.get(st.id);
      let child: { id: string | null; name: string | null } | undefined =
        c.streams.find(ch => ch.parent === st.id && m && ch.market === m.market);
      if (child && !c.on[child.id!]) child = { id: null, name: null };
      o.market = m && child ? { ...m, stream: child.id, name: child.name } : null;
    }
  }
  const stable = out.map((o, i) => [o, i] as const).sort((a, b) => {
    const na = a[0].score == null ? 1 : 0, nb = b[0].score == null ? 1 : 0;
    return na - nb || -(a[0].score || 0) + (b[0].score || 0) || a[1] - b[1];
  }).map(p => p[0]);
  return { id: x.id, scored_at: sc ? sc.at : null, not_scored: sc ? null : await notScored(sql, c, x), floor: c.floor,
    model: name(c.model), streams: stable };
}
