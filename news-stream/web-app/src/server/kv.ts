import type { Sql } from "./db";

export async function kvGet<T>(sql: Sql, k: string, d: T): Promise<T> {
  const [r] = await sql`SELECT v FROM kv WHERE k = ${k}`;
  return r && r.v != null ? (r.v as T) : d;
}

/** ai.model_name: OpenRouter's display name of a model id (kv openrouter_models), without the vendor prefix */
export async function modelNames(sql: Sql): Promise<(id: string) => string> {
  const d = await kvGet<{ models?: Record<string, { name?: string }> }>(sql, "openrouter_models", {});
  const m = d.models || {};
  return (id: string) => (m[id]?.name || id).replace(/^[^:]+:\s*/, "");
}
