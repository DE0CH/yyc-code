// 串流's data (fns.ts fetchFeed): one HKT day of every shown stream's items, the days that have any, and the
// poller's last check of its sources. The day and the order are the notification's (Deyao, 2026-10-20: 串流 mirrors the
// stream's Telegram/WhatsApp groups): an item is filed (item.seen, HKT ISO) and sent seconds later, in stream_items.id
// order (telegram_alerts / whatsapp_alerts), so a day is the items whose seen falls on it, newest (highest id) first.
// stream_items.day is the post's publish day (the row's key), not used here. Public: an item's score and the model's reason are for approved accounts only.
import type { Sql } from "./db";
import { config } from "./config";
import { ITEM_COLS, ITEM_JOIN, itemDisplay } from "./titles";

/** the HKT day an item was filed and sent: item.seen is written as HKT ISO (streams.stream_item), so its first ten
 *  characters are that day (index stream_items_sent_day, corpus.py) */
export const SENT_DAY = "left(si.item->>'seen', 10)";

export const hktToday = () => new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);

export async function archiveDay(sql: Sql, day: string) {
  const rows = await sql.unsafe(`SELECT si.item, ${ITEM_COLS}, p.id AS p_id, p.published AS p_published,
      p.date_only AS p_date_only FROM stream_items si ${ITEM_JOIN} WHERE ${SENT_DAY}=$1 ORDER BY si.id DESC`, [day]);
  return rows.map(r => {
    let it = itemDisplay({ ...(r.item as object) }, r.ai_title, r.ai_title_kind, r.ai_title_model, r.ai_title_at, r.p_title, r.p_text);
    if (r.p_id != null) it = { ...it, published: r.p_published, date_only: !!r.p_date_only };
    return it;
  });
}

export async function archiveDays(sql: Sql): Promise<string[]> {
  // a skip scan (one index probe per day) instead of reading every stream item: 41.9 ms → 0.73 ms on pg-1, the same days
  return (await sql.unsafe(`WITH RECURSIVE d(day) AS (
      (SELECT ${SENT_DAY} FROM stream_items si ORDER BY 1 LIMIT 1)
      UNION ALL
      SELECT (SELECT ${SENT_DAY} FROM stream_items si WHERE ${SENT_DAY} > d.day ORDER BY 1 LIMIT 1) FROM d WHERE d.day IS NOT NULL)
    SELECT day FROM d WHERE day IS NOT NULL`)).map(r => r.day as string);
}

export async function pollStatus(sql: Sql) {
  const rows = await sql`SELECT k, v FROM poll_state WHERE k IN ('last_poll', 'source_status')`;
  const m = Object.fromEntries(rows.map(r => [r.k, r.v]));
  return { last_poll: (m.last_poll as string) ?? null, source_status: (m.source_status as Record<string, unknown>) || {} };
}

export async function feed(sql: Sql, date?: string | null) {
  const today = hktToday();
  const day = date || today;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new RangeError("date must be YYYY-MM-DD");
  const c = await config(sql);
  const shown = c.streams.filter(s => c.shown.includes(s.id));
  const ids = new Set(shown.map(s => s.id));
  const [all, days, st] = await Promise.all([archiveDay(sql, day), archiveDays(sql), pollStatus(sql)]);
  const items = all.filter(it => ids.has(it.stream)).map(it => {
    const { score: _s, reason: _r, ...rest } = it;  // eslint-disable-line @typescript-eslint/no-unused-vars
    return rest;
  });
  // each stream's list length that day and its sentiment counts (串流's bars: they depend on which sources and keywords
  // we follow, so the page puts the selection-bias 「?」 by them)
  const SENTS = c.sentiments;
  const counts: Record<string, Record<string, number>> = {};
  for (const it of items) {
    const k = it.stream as string;
    const x = counts[k] || (counts[k] = { total: 0, day: 0, ...Object.fromEntries(SENTS.map(s => [s, 0])) });
    x.total += 1; x.day += 1;
    if (SENTS.includes(it.sentiment as string)) x[it.sentiment as string] += 1;
  }
  return {
    streams: shown.map(s => ({ id: s.id, name: s.name, title: s.title, categories: s.categories || [] })),
    date: day, today, days, items, counts, last_poll: st.last_poll, source_status: st.source_status, sources: c.sources ?? 0,
    served_at: new Date().toISOString(),  // the server's clock: the page measures last_poll's age on it
  };
}
