// Small public reads: the profiles list (/api/profiles) and the flags of the site the reader is on (/api/flags).
import type { Sql } from "./db";
import { config, flagsFor } from "./config";
import { hktToday } from "./time";

export async function profiles(sql: Sql) {
  const c = await config(sql);
  const stats = await corpusStats(sql);
  return {
    profiles: c.profiles.map(p => ({ id: p.id, name: p.name, title: p.title, kind: p.kind, brief: p.brief })),
    today: hktToday(),
    labels: c.labels, sentiments: c.sent_emoji, corpus: stats, source_types: c.source_types, regions: c.regions,
  };
}

let statsCache: { at: number; v: unknown } | null = null;
type CorpusStats = { posts: number; first_day: string | null; by_type: Record<string, number> };
/** corpus.stats: the monitor counts every post every 10 minutes into kv corpus_stats (monitor.py corpus_counts);
 * read here at most once a minute per isolate. Counting here instead, every isolate read all of posts (2026-10-27).
 * Before the monitor's first count (a new database) this counts once itself. */
export async function corpusStats(sql: Sql): Promise<CorpusStats> {
  if (statsCache && Date.now() - statsCache.at < 60_000) return statsCache.v as CorpusStats;
  const [row] = await sql`SELECT v FROM kv WHERE k = 'corpus_stats'`;
  let v: CorpusStats;
  if (row?.v) {
    const k = row.v as CorpusStats;
    v = { posts: k.posts, first_day: k.first_day ?? null, by_type: k.by_type };
  } else {
    const [[r], types] = await Promise.all([
      sql`SELECT count(*) AS n, min(day) AS first FROM posts`,
      sql`SELECT source_type, count(*) AS n FROM posts GROUP BY 1`,
    ]);
    v = { posts: r.n as number, first_day: (r.first as string) ?? null,
      by_type: Object.fromEntries(types.map(t => [t.source_type ?? "null", t.n as number])) };
  }
  statsCache = { at: Date.now(), v };
  return v;
}

export async function flags(sql: Sql, host: string) {
  return flagsFor(await config(sql), host);
}
