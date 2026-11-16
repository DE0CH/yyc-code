// What the gate keeps, in database mcp (role yyc_mcp, owner): the personal tokens (only their SHA-256 is kept), the
// per-account rate limit and the call log (audit). Who may call is
// not here: it is the access catalogue (catalogue.ts). Every statement is atomic on its own, so any number of replicas
// share it.
import type { Sql } from "../../web-app/src/server/db";
import { isUser } from "./catalogue";
import type { Env } from "./env";

export interface TokenRow { id: string; email: string; label: string; created_at: string; expires_at: string | null;
  last_used_at: string | null; revoked_at: string | null }

export const SCHEMA = `
  CREATE TABLE IF NOT EXISTS tokens (id text PRIMARY KEY, hash text UNIQUE NOT NULL, email text NOT NULL,
    label text NOT NULL, created_at timestamptz NOT NULL, expires_at timestamptz, last_used_at timestamptz,
    revoked_at timestamptz);
  CREATE TABLE IF NOT EXISTS calls (id bigserial PRIMARY KEY, at timestamptz NOT NULL, email text NOT NULL,
    tool text NOT NULL, via text NOT NULL, client text, status integer NOT NULL, ms integer, args text);
  CREATE INDEX IF NOT EXISTS calls_at ON calls (at);
  CREATE INDEX IF NOT EXISTS calls_email ON calls (email, id);
  CREATE TABLE IF NOT EXISTS rate (email text NOT NULL, minute bigint NOT NULL, n integer NOT NULL,
    PRIMARY KEY (email, minute));
  DROP TABLE IF EXISTS oauth_kv;`;  /* the OAuth provider's store; OAuth sign-in for AI apps is gone (2026-11-04) */

export async function sha256(s: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("");
}

const isoRow = <T extends Record<string, unknown>>(r: T): T =>
  Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v instanceof Date ? v.toISOString() : v])) as T;

export class Registry {
  constructor(private env: Env, private sql: Sql) {}

  /** before every protected request (one MCP message or one REST call): on the catalogue (a service token always is)? within the rate limit? (one counting
   *  statement, atomic across replicas) */
  async admit(email: string, service = false): Promise<{ ok: boolean; retryAfter?: number }> {
    email = email.toLowerCase();
    if (!service && !isUser(this.env, email)) return { ok: false };
    const minute = Math.floor(Date.now() / 60000);
    const [{ n }] = await this.sql`INSERT INTO rate (email, minute, n) VALUES (${email}, ${minute}, 1)
      ON CONFLICT (email, minute) DO UPDATE SET n = rate.n + 1 RETURNING n`;
    const limit = Number(this.env.RATE_PER_MIN || 120);
    if ((n as number) > limit) return { ok: false, retryAfter: 60 - Math.floor((Date.now() / 1000) % 60) };
    return { ok: true };
  }

  async log(c: { email: string; tool: string; via: string; client?: string | null; status: number; ms: number; args?: unknown }) {
    const args = c.args === undefined ? null : JSON.stringify(c.args).slice(0, 2000);
    await this.sql`INSERT INTO calls (at, email, tool, via, client, status, ms, args)
      VALUES (now(), ${c.email.toLowerCase()}, ${c.tool}, ${c.via}, ${c.client ?? null}, ${c.status}, ${Math.round(c.ms)}, ${args})`;
  }

  async audit(limit = 200, email?: string) {
    const lim = Math.max(1, Math.min(5000, limit));
    const rows = email
      ? await this.sql`SELECT at, email, tool, via, client, status, ms, args FROM calls WHERE email = ${email.toLowerCase()} ORDER BY id DESC LIMIT ${lim}`
      : await this.sql`SELECT at, email, tool, via, client, status, ms, args FROM calls ORDER BY id DESC LIMIT ${lim}`;
    return rows.map(r => isoRow(r));
  }

  // ---- personal tokens ----

  async createToken(email: string, label: string, days: number | null): Promise<{ id: string; token: string }> {
    const raw = new Uint8Array(32);
    crypto.getRandomValues(raw);
    const token = "yyc_pat_" + btoa(String.fromCharCode(...raw)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const id = crypto.randomUUID();
    const exp = days ? new Date(Date.now() + days * 864e5) : null;
    await this.sql`INSERT INTO tokens (id, hash, email, label, created_at, expires_at)
      VALUES (${id}, ${await sha256(token)}, ${email.toLowerCase()}, ${label.slice(0, 80) || "token"}, now(), ${exp})`;
    return { id, token };
  }

  async tokens(email: string): Promise<TokenRow[]> {
    return (await this.sql`SELECT id, email, label, created_at, expires_at, last_used_at, revoked_at FROM tokens
      WHERE email = ${email.toLowerCase()} ORDER BY created_at DESC`).map(r => isoRow(r)) as unknown as TokenRow[];
  }

  async revokeToken(email: string, id: string): Promise<boolean> {
    const r = await this.sql`UPDATE tokens SET revoked_at = now() WHERE id = ${id} AND email = ${email.toLowerCase()} AND revoked_at IS NULL`;
    return r.count > 0;
  }

  /** a presented personal token → its account, or null (unknown, revoked or expired) */
  async resolveToken(token: string): Promise<{ email: string } | null> {
    const [r] = await this.sql`UPDATE tokens SET last_used_at = now() WHERE hash = ${await sha256(token)}
      AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now()) RETURNING email`;
    return r ? { email: r.email as string } : null;
  }

  /** daily (CronJob mcp-cleanup): the rate counters and calls older than a year go */
  async cleanup() {
    await this.sql`DELETE FROM rate WHERE minute < ${Math.floor(Date.now() / 60000) - 10}`;
    await this.sql`DELETE FROM calls WHERE at < now() - interval '365 days'`;
  }
}

export const registry = (env: Env) => env.REGISTRY;
