// edge:config (news-stream/edge_config.py): what the Python side publishes for the site every minute: the streams,
// which are shown, their thresholds, the feature flags per host, the labels and their meanings, the constants the
// pages show. The Worker never hard-codes these.
import type { Sql } from "./db";
import { MAIN } from "../lib/dashboard_url";

/** the main site's host (flags.py MAIN_HOST) */
const MAIN_HOST = new URL(MAIN).host;

export interface StreamDef {
  id: string; name: string; title?: string; categories?: string[]; parent?: string; split?: string[];
  market?: string; flag?: string; telegram?: boolean;
}
export interface LabelDef { emoji: string; group: string; meaning: string }
export interface LabelInfo { zh: string; meaning: string; group: string; group_zh: string; emoji: string }
export interface EdgeConfig {
  at: string; streams: StreamDef[]; shown: string[]; scored: string[]; on: Record<string, boolean>;
  thresholds: Record<string, number>;
  profiles: { id: string; name: string; title: string; kind: string; brief: string; search_terms?: string[] }[];
  flags: Record<string, Record<string, boolean>>; sources: number | null; floor: number; max_age_s: number;
  hash_sql: string; labels: Record<string, LabelDef>; label_order: string[]; sentiments: string[]; sent_emoji: Record<string, string>;
  label_info: Record<string, LabelInfo>; source_types: string[]; regions: string[]; model: string;
  via: Record<string, string>; images: { threshold: number; min_side: number; tries: number };
  thin: { threshold: number; wait_s: number; label: string; waits_zh: Record<string, string> };
  item: { extract: number; comment_extract: number; coverage_days: number; coverage_min: number; coverage_max: number; article_max: number };
  /** the posts the full-text job reads (fulltext_fetch.will_read) and the post URLs whose comments a fetcher reads
   *  (twopass.UNIT_URLS); absent in a document published before 2026-10-16 */
  fulltext?: { types: string[]; sids?: string[]; full_chars: number; from_seen?: string; skip_hosts: string[]; youtube_sid?: string; youtube_url?: string };
  comment_urls?: string[];
  /** the catalogue sources (news-stream/sites.json, in its order): the proof view's ?catalogue=1 walks them */
  catalogue?: { id: string; name: string; url: string; kind: string; type: string; region: string }[];
  /** since when every stream's score is stored, 0–19 too (kv scores_all_from, streams.file); null before the first */
  all_scores_from?: string | null;
  /** two-stage scoring (streams.SCREEN, streams.CONFIRM_MODEL): stage 1's score from which stage 2 scores the pair
   *  again, and stage 2's model; absent in a document published before 2026-10-22 */
  screen?: number; confirm_model?: string;
  models?: { stage1: string; stage2: string };  // the two stages' models by display name (edge_config.py)
}

const TTL_MS = 30_000;
let cached: { at: number; v: EdgeConfig } | null = null;

export async function config(sql: Sql): Promise<EdgeConfig> {
  if (cached && Date.now() - cached.at < TTL_MS) return cached.v;
  const [r] = await sql`SELECT v FROM kv WHERE k = 'edge:config'`;
  if (!r) throw new Error("edge:config is not published yet (edge_config.py)");
  cached = { at: Date.now(), v: r.v as EdgeConfig };
  return cached.v;
}

/** the flags of the site the reader is on (flags.py: the main site, or the beta site with every flag on) */
export function flagsFor(c: EdgeConfig, host: string): Record<string, boolean> {
  const h = (host || "").split(":")[0].toLowerCase();
  return c.flags[h] || c.flags[MAIN_HOST] || {};
}

/** a pipeline flag (flags.py `pictures`, `transcription`: the same on every site); absent = off, like flags.py */
export const pipelineOn = (c: EdgeConfig, name: string) => !!flagsFor(c, MAIN_HOST)[name];

export const forget = () => { cached = null; };
