// 報告: the files reports.py builds every day, stored in Postgres (table report_files) and served from here at the
// URLs /reports/<day>/<profile>/<file> (links in sent emails use them), listed by /api/reports (the same listing
// as reports.listing).
import type { Sql } from "./db";

/** what a /reports/ file URL can name */
export const FILE_URL = /^\/reports\/(\d{4}-\d{2}-\d{2})\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_.-]+)$/;
/** the extensions served (reports.TYPES); any other answers 404 */
const SERVED = new Set([".html", ".pdf", ".png", ".json", ".xlsx", ".docx", ".csv", ".js", ".css", ".svg", ".txt"]);

export interface ReportEntry { day: string; profile: string; files: string[] }

/** the newest `days` days' reports, newest day first, profiles and files in name order */
export async function listing(sql: Sql, days = 60): Promise<ReportEntry[]> {
  const rows = await sql`
    SELECT day, profile, json_agg(name ORDER BY name COLLATE "C") AS files FROM report_files
    WHERE day IN (SELECT DISTINCT day FROM report_files ORDER BY day DESC LIMIT ${days})
    GROUP BY day, profile ORDER BY day COLLATE "C" DESC, profile COLLATE "C"`;
  return rows.map(r => ({ day: r.day as string, profile: r.profile as string,
    files: (r.files as string[]).map(f => `/reports/${r.day}/${r.profile}/${f}`) }));
}

/** one stored file: its Content-Type and bytes, or null */
export async function stored(sql: Sql, day: string, profile: string, name: string): Promise<{ ctype: string; body: Uint8Array } | null> {
  const dot = name.lastIndexOf(".");
  if (dot <= 0 || !SERVED.has(name.slice(dot))) return null;  // os.path.splitext: a leading dot is no extension
  const [r] = await sql`SELECT ctype, body FROM report_files WHERE day = ${day} AND profile = ${profile} AND name = ${name}`;
  return r ? { ctype: r.ctype as string, body: r.body as Uint8Array } : null;
}

/** for the parity test (tests/edge_parity.py reports): stored files as {ctype, sha, len}, or null each */
export async function storedDigests(sql: Sql, files: [string, string, string][]) {
  const { createHash } = await import("node:crypto");
  const out = [];
  for (const [day, profile, name] of files) {
    const hit = await stored(sql, day, profile, name);
    out.push(hit && { ctype: hit.ctype, sha: createHash("sha256").update(hit.body).digest("hex"), len: hit.body.length });
  }
  return out;
}
