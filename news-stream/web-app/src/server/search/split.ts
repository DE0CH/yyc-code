// The search's word/substring split (搜尋's run and export, yyc-mcp's posts_search).
// The search's one query (search.where + rankSql) checks every candidate post's stored words and text: for a common
// word (香港: 131k posts in 30 days) that reads the text of every post it matches, twice (the page and the count), and
// runs past the 20 s statement timeout. Its answer is the posts the words match (rank 1, newest first), then the posts
// that match only as a substring (rank 0). For a query of words alone (one term, or terms any of which may match) the
// same answer comes from: a = the word matches, found in the words index alone (no stored text read); b = the posts
// whose two-character grams hold a term (the grams index), outside a, whose text holds a term as a substring (only
// their text is read). Exact: a ⊆ the query's matches, and every substring-only match is a grams candidate. A term in
// Latin letters (trump) still reads every grams candidate's text: its bigrams are common, so b reads ~30k texts.
import { cond, prefilter, rankSql, type Node, type Parsed } from "./query";
import type { SearchText } from "./searchtext";
import { COLS, where, type P, type Search } from "./search";

const WORD = "coalesce(p.words @@ %s::tsquery, FALSE)";
/** each `pat` in a %s-placeholder SQL text (pat holds one %s) → `to`, keeping or dropping its argument */
function rewrite(text: string, args: P[], pat: string, to: string, keep: boolean): [string, P[]] {
  const re = new RegExp(pat.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "|%s", "g");
  const out: P[] = [];
  let i = 0;
  const t = text.replace(re, m => {
    const v = args[i++];
    if (m === "%s" || keep) out.push(v);
    return m === "%s" ? m : to;
  });
  if (i !== args.length) throw new Error("rewrite: placeholders and arguments differ");
  return [t, out];
}
const wordsOnly = (n: Node | null): boolean => !!n && (n[0] === "term" || (n[0] === "or" && n[1].every(wordsOnly)));

/** the CTE a (the word matches) and its args, plus the parts b needs, when the query is words alone sorted by date;
 *  else null. `x`, `xargs`: more filters on p (yyc-mcp's platforms and stream) */
function parts(st: SearchText, s: Search, x: string, xargs: P[]) {
  const q: Parsed = s.parsed;
  if (!wordsOnly(q.node) || q.inc.length || q.exc.length || q.topic_only || s.sort === "relevance") return null;
  const pre = prefilter(st, q);
  if (!pre) return null;
  const [f0, fa0] = where(st, { ...s, parsed: { node: null, topic_only: false, inc: [], exc: [] } });
  const [f, fa] = [f0 + x, [...fa0, ...xargs]];
  const [r0, ra0] = rankSql(st, q);
  const [r, ra] = rewrite(r0.replace(/::int$/, ""), ra0, WORD, "p.words @@ %s::tsquery", true);
  const [g, ga] = rewrite(pre[0], pre[1], "p.words @@ %s::tsquery OR ", "", false);
  const [c0, ca0] = cond(st, q.node, "all");
  const [c, ca] = rewrite(c0, ca0, WORD, "FALSE", false);  // b's posts: no word matches (they are in a)
  const sel = "SELECT p.id, coalesce(p.published, p.seen) AS t, p.source_type, p.website FROM posts p";
  return { dir: s.sort === "oldest" ? "ASC" : "DESC", a: `a AS MATERIALIZED (${sel} WHERE ${f} AND ${r})`, aArgs: [...fa, ...ra],
    // b: NOT IN (a) comes before the substring test in the scan's filter, so a word match's text is never read
    b: `b AS MATERIALIZED (${sel} WHERE ${f} AND (${g} OR p.grams IS NULL) AND NOT (p.id IN (SELECT id FROM a)) AND ${c})`,
    bArgs: [...fa, ...ga, ...ca] };
}

/**
 * The split query (text, args; LIMIT and OFFSET last) when the query is words alone, sorted by date; else null.
 * Its rows: the page (word, COLS), newest (oldest) first, word matches before substring-only ones; with `head` each
 * row (and one row of NULLs when the page is empty) also carries `total` (the count of every match) or `facets`
 * (jsonb [{source_type, website, n}] over every match).
 */
export function splitSql(st: SearchText, s: Search, x: string, xargs: P[], head: "total" | "facets" | null): [string, P[]] | null {
  const sp = parts(st, s, x, xargs);
  if (!sp) return null;
  const { dir } = sp;
  // facets only on the first row (each row carrying them sent the whole list up to 100 times)
  const top = head === "total" ? "(SELECT count(*) FROM m) AS total, "
    : head === "facets" ? "CASE WHEN pg.rn IS NULL OR pg.rn = 1 THEN (SELECT coalesce(jsonb_agg(f), '[]') FROM f) END AS facets, " : "";
  const from = head ? "(SELECT 1) one LEFT JOIN (pg JOIN posts p ON p.id = pg.id) ON TRUE" : "pg JOIN posts p ON p.id = pg.id";
  const text = `WITH ${sp.a}, ${sp.b},
    m AS (SELECT id, t, source_type, website, 1 AS word FROM a UNION ALL SELECT id, t, source_type, website, 0 FROM b),
    ${head === "facets" ? "f AS (SELECT source_type, website, count(*) AS n FROM m GROUP BY 1, 2)," : ""}
    pg AS (SELECT *, row_number() OVER (ORDER BY word DESC, t ${dir}, id ${dir}) AS rn FROM
      (SELECT * FROM m ORDER BY word DESC, t ${dir}, id ${dir} LIMIT %s OFFSET %s) page)
    SELECT ${top}pg.word, ${COLS} FROM ${from} ORDER BY pg.word DESC, pg.t ${dir}, pg.id ${dir}`;
  return [text, [...sp.aArgs, ...sp.bArgs]];
}

/** the word matches alone (text, args; LIMIT and OFFSET last) when splitSql applies; else null. Its rows: the page of
 *  word matches (word = 1, COLS) and `n`, the count of every word match (one row of NULLs and n when the page is empty) */
export function wordSql(st: SearchText, s: Search, x: string, xargs: P[]): [string, P[]] | null {
  const sp = parts(st, s, x, xargs);
  if (!sp) return null;
  const { dir } = sp;
  const text = `WITH ${sp.a},
    pg AS (SELECT * FROM a ORDER BY t ${dir}, id ${dir} LIMIT %s OFFSET %s)
    SELECT (SELECT count(*) FROM a) AS n, 1 AS word, ${COLS} FROM (SELECT 1) one
    LEFT JOIN (pg JOIN posts p ON p.id = pg.id) ON TRUE ORDER BY pg.t ${dir}, pg.id ${dir}`;
  return [text, sp.aArgs];
}
