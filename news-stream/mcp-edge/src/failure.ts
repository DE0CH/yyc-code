// What a caller is told when a tool fails on the server (not the caller's mistake, which is a ToolError's own words):
// a cause the caller can act on, named; anything else stays "the tool failed on the server" (no internals leave).

/** Postgres stopped the statement at its statement_timeout (the roles' 30 s: deploy/db/postgres.yaml). */
const STATEMENT_TIMEOUT = "57014";

export function failure(e: unknown): string {
  if ((e as { code?: unknown } | null)?.code === STATEMENT_TIMEOUT)
    return "the search ran past the database's 30 s time limit and was stopped; fewer days or more specific words finish sooner";
  return "the tool failed on the server";
}
