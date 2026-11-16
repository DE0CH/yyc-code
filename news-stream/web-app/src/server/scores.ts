// 串流's score line (approved accounts): /app/stream/scores. An item filed without a score of its own shows
// its post's latest score from the scores table, marked "from": "scores". The day is the notification's (feed.ts SENT_DAY).
import type { Sql } from "./db";
import { config } from "./config";

export async function streamScores(sql: Sql, day: string) {
  const c = await config(sql);
  const rows = await sql`SELECT i.stream, i.url, (i.item->>'score')::int AS own, i.item->>'reason' AS r, s.score AS bs, s.reason AS br
      FROM stream_items i LEFT JOIN posts p ON p.url = i.url
      LEFT JOIN scores s ON s.stream = i.stream AND s.post_id = p.id
        AND NOT (s.score < ${c.floor} AND (s.model IS NULL OR s.model = ${c.model}))  -- item.hiddenLow: as before 21 Oct
      WHERE left(i.item->>'seen', 10) = ${day} AND (i.item->>'score' IS NOT NULL OR s.score IS NOT NULL)`;
  const out: Record<string, { score: number; reason: string | null; from?: string }> = {};
  for (const x of rows) out[`${x.stream}|${x.url}`] = x.own != null ? { score: x.own, reason: x.r } : { score: x.bs, reason: x.br, from: "scores" };
  return out;
}
