// 數據分析's text box (insights.select's `text`): query.parse, then corpus.match_ids / corpus.word_matched, with the
// search section's port of query.py (server/search/query.ts) and searchtext.py (server/search/searchtext.ts).
import type { Sql } from "./db";
import { learn, parse, pg, PyTypeError, QueryError, rankSql, toSql } from "./search/query";
import { searchText } from "./search/searchtext";

/** a query error the page shows: a QueryError answers 400 and a PyTypeError 404 */
export class TextError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

/** the ids among `ids` (the profile's posts) the query matches, in corpus.match_ids order (word matches first, then
 *  substring-only ones, each newest first), and which of them match by words (corpus.word_matched). Limiting
 *  match_ids to the profile's posts changes nothing: insights.select keeps only those. */
export async function textMatch(sql: Sql, text: string, start: string, end: string, ids: number[]): Promise<{ order: number[]; words: Set<number> }> {
  const st = await searchText(sql);
  let q;
  try {
    q = parse(text, st);
    await learn(sql, st, q);
  } catch (e) {
    if (e instanceof QueryError) throw new TextError(400, e.message);
    if (e instanceof PyTypeError) throw new TextError(404, "no such profile");
    throw e;
  }
  let where, params, rank, rargs;
  try {
    [where, params] = toSql(st, q);
    [rank, rargs] = rankSql(st, q);
  } catch (e) {
    if (e instanceof QueryError) throw new TextError(400, e.message);
    if (e instanceof PyTypeError) throw new TextError(404, "no such profile");
    throw e;
  }
  if (!ids.length) return { order: [], words: new Set() };
  const text_ = `SELECT p.id, ${rank} AS r FROM posts p WHERE p.day BETWEEN %s AND %s AND p.id = ANY(%s::bigint[]) AND (${where})
      ORDER BY ${rank} DESC, coalesce(p.published, p.seen) DESC, p.id DESC`;
  const args = [...rargs, start, end, "{" + ids.join(",") + "}", ...params, ...rargs];
  const rows = await sql.unsafe(pg(text_), args as never[]);
  return { order: rows.map(r => Number(r.id)), words: new Set(rows.filter(r => Number(r.r) === 1).map(r => Number(r.id))) };
}
