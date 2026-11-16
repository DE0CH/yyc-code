// 數據分析's Excel exports (/api/export/<summary|posts|hot_topics|authors|sources|trend|themes>.xlsx): the columns,
// sheets and meta rows of each (web.export). Summary, trend, authors and sources depend on which sources and keywords
// YYC follows: their header has a Note line saying so (lib/bias.ts; README "Figures that depend on our selection").
import type { Row, Sql } from "./db";
import { ApiError, fillText, hotTopicRows, pyInt, selected, sentimentIndex, type Q } from "./dashboard";
import { summaryApi, topAuthors, topSources, trend } from "./analytics";
import { BIAS_NOTE, BIAS_NOTE_EN } from "../lib/bias";
import { attachment, xlsx, type Cell, type Sheet } from "./xlsx";
import { hktToday } from "./time";

export const EXPORTS = ["summary", "posts", "hot_topics", "authors", "sources", "trend", "themes"] as const;
const BIASED = new Set(["summary", "trend", "authors", "sources"]);

const POST_COLS = ["Topic", "Author", "Publish Date", "Source Type", "Web Site", "Board", "Region", "Sentiment", "Labels",
  "Theme", "Content", "Link"];

/** titles.plain: the title as text, 〔AI標題〕 before an AI-written title, 〔連結標題〕 before a linked page's */
const plain = (x: Row) => (x.title_by_ai ? "〔AI標題〕" : x.title_linked ? "〔連結標題〕" : "") + (x.title || "");

const postRow = (x: Row): Cell[] => [plain(x), x.author, x.published || x.seen, x.source_type, x.website, x.board, x.region,
  x.sentiment, (x.labels || []).join(" "), x.theme, Array.from((x.text || "") as string).slice(0, 2000).join(""), x.url];

const HOT_COLS = ["Top", "Title", "Web Site", "Board", "Sentiment Level", "Sentiment Index (%)", "Sentiment Score",
  "Contribution (%)", "All Post Count", "Period Post Count", "Matched Post Count", "Participant Count",
  "View Count", "Correlation Index (%)", "Initial Date", "End Date", "Discussion Days", "Original Link", "Topic ID"];

const hotRow = (i: number, t: Row): Cell[] => [i, t.title, t.website, t.board, t.sentiment_level, t.sentiment_index, t.sentiment_score,
  t.contribution, t.all_post_count, t.period_post_count, t.matched_post_count, t.participants,
  t.view_count == null ? "N/A" : t.view_count, t.correlation_index, t.initial_date, t.end_date, t.day_span, t.url, t.id];

/** web.export → [file name, bytes] */
export async function exportXlsx(sql: Sql, kind: string, q: Q): Promise<[string, Uint8Array]> {
  if (!(EXPORTS as readonly string[]).includes(kind)) throw new ApiError(400, "unknown export");
  const { c, pid, start, end, ps } = await selected(sql, q);
  const p = c.profiles.find(x => x.id === pid)!;
  const meta: [string, Cell][] = [["Profile Name", p.name], ["Posts", `AI score ${c.thresholds[pid] ?? 50} or more (0-100) against the profile's scope`],
    ["Scope", p.brief || ""], ["Search Period", `${start} to ${end}`], ["Report Generation Date", hktToday()]];
  if (BIASED.has(kind)) meta.push(["Note", `${BIAS_NOTE} ${BIAS_NOTE_EN}`]);
  let sheets: Sheet[];
  const top = () => (q.top ? pyInt(q.top) : 100);
  if (kind === "authors") {
    sheets = [["Top Author", ["Author", "Web Site", "Posts", "Topics", "Sentiment Index (%)", "Sentiment Level",
      "Contribution (%)", "View Count", "Most Active Board"],
      topAuthors(ps, top()).map(r => [r.author, r.website, r.posts, r.topics, r.sentiment_index, r.sentiment_level,
        r.contribution, r.view_count, r.most_active_board])]];
  } else if (kind === "sources") {
    sheets = [["Top Source", ["Web Site", "Board", "Source Type", "Region", "Posts", "Topics", "Participants",
      "Sentiment Index (%)", "Sentiment Level", "Contribution (%)"],
      topSources(ps, top(), q.by || "website").map(r => [r.website, r.board, r.source_type, r.region, r.posts, r.topics,
        r.participants, r.sentiment_index, r.sentiment_level, r.contribution])]];
  } else if (kind === "trend") {
    const rows = await trend(sql, c, pid, start, end, q.bucket || "day") as Row[];
    sheets = [["Exposure & Mentioning Trend", ["Period", "Posts", "Topics", "Authors", "View Count", "Sentiment Index (%)"],
      rows.map(r => [r.period, r.posts, r.topics, r.authors, r.views, r.sentiment_index])]];
  } else if (kind === "summary") {
    const s = await summaryApi(sql, { p: pid, from: start, to: end });
    sheets = [["Summary", ["Metric", "Value"], Object.entries(s.metrics).filter(([, v]) => v === null || typeof v !== "object")
      .map(([k, v]) => [k, v as Cell])],
      ["Trend", ["Period", "Posts", "Sentiment Index (%)"], (s.trend as Row[]).map(r => [r.period, r.posts, r.sentiment_index])]];
  } else if (kind === "posts") {
    await fillText(sql, ps);
    sheets = [["Post Listing", POST_COLS, ps.map(postRow)]];
  } else if (kind === "hot_topics") {
    const top = q.top ? pyInt(q.top) : 100;
    sheets = [["Hot Topic Listing", HOT_COLS, (await hotTopicRows(sql, ps, top)).map((t, i) => hotRow(i + 1, t))]];
  } else {
    await fillText(sql, ps);
    const groups = new Map<string, Row[]>();
    for (const x of ps) { const k = x.theme || "(not themed yet)"; if (!groups.has(k)) groups.set(k, []); groups.get(k)!.push(x); }
    const sorted = [...groups.entries()].map((e, i) => [e, i] as const).sort((a, b) => b[0][1].length - a[0][1].length || a[1] - b[1]).map(x => x[0]);
    sheets = [["Themes", ["Theme", "Posts", "Sentiment Index (%)"], sorted.map(([k, v]) => [k, v.length, sentimentIndex(v)])],
      ["Posts by Theme", ["Theme_Tag", ...POST_COLS], ps.map(x => [x.theme, ...postRow(x)])]];
  }
  return [`${pid}_${kind}_${start}_${end}.xlsx`, await xlsx(sheets, meta)];
}

/** the download as web.download sends it */
export async function exportResponse(sql: Sql, kind: string, q: Q): Promise<Response> {
  try {
    const [name, data] = await exportXlsx(sql, kind, q);
    return new Response(data as BodyInit, { status: 200, headers: {
      "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "Content-Disposition": attachment(name), "Cache-Control": "private, no-store" } });
  } catch (e) {
    if (e instanceof ApiError) return new Response(JSON.stringify({ error: e.message }), { status: e.status,
      headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "private, no-store" } });
    throw e;
  }
}
