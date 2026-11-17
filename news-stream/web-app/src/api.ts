// THE data module: every request the app makes goes through here. Public data comes from the Worker's server
// functions (fns.ts: Postgres read on the Worker); what is for approved accounts from /app/… routes behind Cloudflare
// Access (/app/me, /app/stream/scores, /app/item/text, /app/item/ai, /app/item/video), answered by the same Worker.
// A page rendered on the server reads the same approved-only data inside the Worker (server/reader.ts: once
// Cloudflare Access has said who the reader is), so it is in the page's HTML; these reads are not server functions
// (a server function is a public URL), only code the server render runs.
import { createIsomorphicFn } from "@tanstack/react-start";
import { fetchFeed, fetchFlags, fetchItem, fetchItemCoverage, fetchProfiles, fetchReports } from "./fns";

import { today } from "./lib/time";
import { withTimeout } from "./lib/freshness";

const BASE = "";

export class HttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function getJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, { credentials: "same-origin", ...init });
  if (!r.ok) {
    const body = (await r.json().catch(() => ({}))) as { error?: string };
    throw new HttpError(r.status, body.error || `HTTP ${r.status}`);
  }
  return r.json() as Promise<T>;
}

const qs = (o: Record<string, string | number | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v != null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? "?" + s : "";
};

// ---------- types (the shapes the current endpoints answer) ----------

export type Sentiment = "positive" | "neutral" | "negative" | "mixed" | "irony";


export interface Stream { id: string; name: string; title: string; categories?: string[] }

/** a title from AI (news-stream/titles.py) for a post with no title of its own: written (title_by_ai, badged) or the
 * post's own opening headline (title_extracted, no badge); or for a post whose own title doesn't say what it is
 * about (title_retitled, badged, the source's title shown as 來源標題); original_title = the title as collected */
export interface AiTitleFields {
  title_by_ai?: boolean; title_extracted?: boolean; title_linked?: boolean; title_retitled?: boolean; original_title?: string | null;
  ai_title_model?: string | null; ai_title_model_name?: string | null; ai_title_at?: string | null;
}

export interface FeedItem extends AiTitleFields {
  sid: string; url: string; title: string; source: string; section: string | null; stream: string;
  /** the post's own text (a post with a title from AI: its full content, up to 2000 characters) */
  text?: string | null;
  seen: string; published: string | null; slot?: string;
  /** the source gave only the day: published is the time first seen within it (ordering); show the day only */
  date_only?: boolean;
  sentiment: Sentiment | null; labels?: string[]; relevant?: boolean;
  category?: string | null;
}

/** the length of each stream's list that day and its sentiment counts */
export interface StreamCounts { total?: number; day: number; positive?: number; neutral?: number; negative?: number; mixed?: number; irony?: number }

export interface Feed {
  streams: Stream[]; date: string; today: string; days: string[]; items: FeedItem[];
  counts: Record<string, StreamCounts>;
  last_poll: string | null; sources: number;
  served_at?: string;  // the server's clock when it answered (lib/freshness.ts)
  source_status: Record<string, { at?: string; ok: boolean; off?: string | boolean; error: string | null; items: number }>;
}

export interface Profile { id: string; name: string; title?: string; kind: "stream" }
export type Flags = Record<string, boolean>;

/** one stream item's relevance score (0–100) and the model's reason; approved accounts only */
export interface Score { score: number; reason?: string; from?: "scores" }  // from: the item was filed unscored, this is the post's latest score in the scores table

export interface ItemTag {
  profile: string; name: string; title: string; kind: string; relevant: boolean; sentiment: Sentiment | null;
  labels: string[]; theme: string | null; tagged_at: string | null; corrected: boolean;
  corrected_by: string | null; corrected_at: string | null;
}
export interface ItemComment { id: number; author: string | null; time: string; text: string; cut: boolean; likes?: number | null; dislikes?: number | null;
  /** the post's latest scoring saw this comment (twopass.py: the divider) */
  scored?: boolean }
export interface ItemRef { id: number; title: string; title_by_ai?: boolean; title_linked?: boolean; website?: string | null; board?: string | null; author?: string | null; source_type?: string | null; time?: string | null }

export interface Item extends AiTitleFields {
  id: number; url: string; /** what 開啟原文 / Original link opens: the article's own address (posts.canonical_url, canonical.py), else url */ link: string; title: string; website: string | null; board: string | null; author: string | null;
  platform: string | null; source_type: string | null; region: string | null; sid: string | null; source: string | null;
  /** a bare day "YYYY-MM-DD" when date_only (the source gave no time) */
  published: string | null; date_only?: boolean; seen: string; day: string; cached_at: string | null;
  comment: boolean; thread_url: string | null;
  extract?: string | null; text_chars?: number | null;
  /** the full-text job (fulltext_fetch.py, every 30 minutes) will read the publisher's page: 正在收集內文 */
  text_due?: boolean;
  /** titles.text_state: full | due | whole | failed | none */
  text_state?: "full" | "due" | "whole" | "failed" | "none";
  /** a post whose comments a fetcher reads (twopass.UNIT_URLS): none yet on a new post = not read yet */
  comments_read?: boolean;
  /** where the stored text came from (paywall.py): the reader's label, and an archive.today copy's link */
  text_via?: string | null; text_via_label?: string | null; text_via_url?: string | null;
  engagement: Record<string, number | null>;
  /** null/absent = the AI tagger has not run yet (處理中) */
  tags?: ItemTag[] | null;
  matched: string[];
  streams: { stream: string; name: string; day: string; category?: string | null }[];
  /** the stream scorer's own sentiment and labels (public); null = not scored, `not_scored` says why */
  scoring?: ItemScoring | null;
  not_scored?: NotScored | null;
  /** the near-empty gate (thin.py): rejected = 「內容不足，未評分」, waiting = held for a transcript / picture text /
   *  a link's content until `until`; `why` is the sentence the page shows */
  thin?: ItemThin | null;
  /** null/absent = comments not collected yet (處理中) */
  thread?: { parent: { id: number; title: string } | null; total: number; stated: number | null; comments: ItemComment[];
    /** when the post's latest scoring (with its comments) ran; comments with `scored` were in it */
    scored_at?: string | null } | null;
  /** posts.article_id: the other posts that are the same content; null = the post is alone */
  article?: { id: number | string; posts: ItemRef[] } | null;
  vocab?: { sentiments: string[]; labels: string[]; label_info?: Record<string, LabelInfo> };
  /** the post's video and audio clips (media.py); the transcripts themselves come with ItemText (approved accounts) */
  media?: ItemMedia[];
  /** the post's saved TikTok video (tiktok_video.py: TikTok posts passing stage 1); the links come with ItemVideoLinks */
  video?: ItemVideo | null;
  /** the post's pictures (images.py), scored for meaning; text read from them comes with ItemText */
  pictures?: { meaningful_from: number; items: ItemPicture[] };
  /** the post's links (item.links): resolved by link following (follow.py) once the post reached a stream, with the
   *  outlet's name and our own copy's id; else as they stand in its text */
  links?: ItemLink[];
}

export interface ItemLink { url: string; host: string | null; name: string | null; item_id: number | null; status: string | null }

export interface ItemPicture {
  url: string; hash: string | null; status: "scored" | "queued" | "failed"; score: number | null; meaningful: boolean;
  reason: string | null; width: number | null; height: number | null; model: string | null; chars: number;
}

/** one clip: queued = waiting for its transcript (逐字稿排隊中), captions = only its YouTube subtitles (transcription off), done (chars = its transcript's length), failed (error) */
export interface ItemVideo {
  status: "queued" | "ok" | "failed" | "gone"; bytes: number | null; duration: number | null;
  width: number | null; height: number | null;
}
/** approved accounts (/app/item/video): presigned links to the file in YYC's bucket, valid until expires_at */
export interface ItemVideoLinks extends ItemVideo {
  url: string | null; download: string | null; filename?: string; expires_at: string | null; error?: string;
}

export interface ItemMedia {
  key: string; host: string | null; kind: string; status: "queued" | "done" | "failed" | "no_direct" | "captions"; duration: number | null;
  model: string | null; model_name: string | null; chars: number; error: string | null; done_at: string | null;
  by_ai: boolean | null;  // a model-made transcript (「AI 轉錄」); false = YouTube's subtitles
  captions_name: string | null; captions_chars: number;  // a YouTube clip's own subtitles (「YouTube 上載者字幕／自動字幕（lang）」)
  /** a queued clip's place in the transcription queue (1 = next); null while it waits to retry */
  queue?: number | null;
}

/** why the scorer has not scored a post: collected before scoring started, a comment (comment scoring off),
 *  published long before it was collected, near-empty (thin: rejected; thin_wait: waiting for content that may still
 *  come), or due (waiting for its full text or its turn) */
export type NotScored = "before" | "comment" | "old" | "thin" | "thin_wait" | "due";
export interface ItemThin {
  status: "waiting" | "rejected"; units: number; threshold: number; waits: string[]; until: string | null;
  label: string; at: string; why: string;
}
export interface ItemScoring { at: string; sentiment: Sentiment | null; labels: string[]; rescoring: boolean;
  /** a unit post's second scoring (twopass.py, with the comments by then) still to come: when it is due (HKT) */
  second_due?: string | null }
export interface LabelInfo { zh: string; meaning: string; group: string; group_zh: string; emoji: string }

/** approved accounts: one stream's score of the post; score null = below `floor` (not stored) or not scored */
export interface StreamScore {
  stream: string; name: string; title: string; score: number | null; reason: string | null; category: string | null;
  scored_at: string | null; threshold: number; passed: boolean; kept: { day: string; score: number | null } | null;
  /** filed in this stream but now under its threshold: one sentence with both times and scores (item.py rescored) */
  rescored?: string | null;
  /** two-stage scoring (streams.confirm): the model whose score this is (stage 2's once stage 1 scored it streams.SCREEN or more),
   *  and stage 1's screening score; null = stage 1's score stands */
  model?: string | null;
  screen?: { score: number; reason: string | null; model: string } | null;
  /** a split stream (Galbot): the market classifier's answer and the stream it went to; null = not classified yet
   *  (分類處理中); absent on other streams */
  market?: { market: string; how: string; note?: string | null; model?: string | null; at?: string | null; stream: string | null; name: string | null } | null;
}
export interface ItemAI { id: number; scored_at: string | null; not_scored: NotScored | null; floor: number; model: string; streams: StreamScore[] }

export interface ItemText { text: string | null; cached_at?: string | null; comments?: Record<string, string>; media?: Record<string, string>; media_captions?: Record<string, string>; pictures?: Record<string, string> }

// ---------- queries ----------

/** one day of the stream archive (HKT); no date = today */
export const getFeed = (date?: string | null) =>
  withTimeout(fetchFeed({ data: { date } }) as unknown as Promise<Feed>).then(f => ({ ...f, today: today(f.today) }));

export const getProfiles = () => (fetchProfiles() as unknown as Promise<{ profiles: Profile[] }>).then(d => d.profiles || []);

export const getFlags = () => (fetchFlags() as unknown as Promise<Flags>).catch(() => ({} as Flags));

/** the approved account's email, or null. The browser asks /app/me (Access answers a redirect to its login for
 *  everyone else); a page rendered on the server asks server/reader.ts, which may also answer undefined: it cannot
 *  tell, and the browser asks */
export const getMe = createIsomorphicFn()
  .server(async (): Promise<string | null | undefined> => (await import("./server/reader")).reader())
  .client(async (): Promise<string | null | undefined> => {
    try {
      const r = await fetch(BASE + "/app/me", { redirect: "manual", credentials: "same-origin" });
      if (!r.ok) return null;
      const d = (await r.json()) as { email?: string | null };
      return d.email || null;
    } catch { return null; }
  });

/** approved accounts: "stream|url" → score for that day */
export const getScores = createIsomorphicFn()
  .server(async (date?: string | null): Promise<Record<string, Score>> => {
    const { approvedRead } = await import("./server/reader");
    const { streamScores } = await import("./server/scores");
    const { hktToday } = await import("./server/time");
    return approvedRead(sql => streamScores(sql, date || hktToday())) as Promise<Record<string, Score>>;
  })
  .client((date?: string | null): Promise<Record<string, Score>> =>
    getJSON<{ scores: Record<string, Score> }>("/app/stream/scores" + qs({ date }), { redirect: "manual" }).then(d => d.scores || {}));

/** one post, or null when the system has no such post (a value, not an error: a failed query rendered on the server
 *  reaches the browser as an unhandled "redacted" error) */
export async function getItem(ref: { id: number } | { url: string }): Promise<Item | null> {
  const d = await fetchItem({ data: "id" in ref ? { id: ref.id } : { url: ref.url } });
  return (d as unknown as Item) || null;
}

/** 同一事件的其他報道: other outlets' posts on the same story (loaded after the page shows) */
export const getItemCoverage = (id: number) => fetchItemCoverage({ data: { id } }) as unknown as Promise<ItemRef[]>;

/** approved accounts: the stream scorer's score, reason, category and threshold for every stream */
export const getItemAI = createIsomorphicFn()
  .server(async (id: number): Promise<ItemAI> => {
    const { approvedRead } = await import("./server/reader");
    const { aiScores } = await import("./server/item");
    return approvedRead(sql => aiScores(sql, id)) as unknown as Promise<ItemAI>;
  })
  .client((id: number) => getJSON<ItemAI>("/app/item/ai" + qs({ id }), { redirect: "manual" }));

/** approved accounts: the full stored text and the thread's comments in full */
export const getItemText = createIsomorphicFn()
  .server(async (id: number): Promise<ItemText> => {
    const { approvedRead } = await import("./server/reader");
    const { text } = await import("./server/item");
    return approvedRead(sql => text(sql, id)) as unknown as Promise<ItemText>;
  })
  .client((id: number) => getJSON<ItemText>("/app/item/text" + qs({ id }), { redirect: "manual" }));

/** approved accounts: the saved TikTok video's presigned links (made per request, valid one hour) */
export const getItemVideo = createIsomorphicFn()
  .server(async (id: number): Promise<ItemVideoLinks> => {
    const { approvedRead } = await import("./server/reader");
    const { s3Config, videoLinks } = await import("./server/video");
    const { env } = await import("cloudflare:workers");
    const v = await approvedRead(sql => videoLinks(sql, id, s3Config(env as unknown as Record<string, unknown>)));
    if (!v) throw new HttpError(404, "no saved video");
    return v as unknown as ItemVideoLinks;
  })
  .client((id: number) => getJSON<ItemVideoLinks>("/app/item/video" + qs({ id }), { redirect: "manual" }));


// ---------- 報告 ----------

/** one profile's reports of one day: the file URLs (/reports/<day>/<profile>/<file>) */
export interface ReportEntry { day: string; profile: string; files: string[] }

/** /api/reports; null when the 報告 section is off for this site */
export const getReports = () => (fetchReports() as unknown as Promise<{ reports: ReportEntry[] } | null>).then(d => d && (d.reports || []));
