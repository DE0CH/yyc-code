// Postgres from the Worker: Cloudflare Hyperdrive (pooled connections to YYC's Postgres, binding HYPERDRIVE) with
// postgres.js. Every data function takes the `Sql` it runs on, so the same code runs in the Worker and in Node
// (scripts/call.ts, the parity tests against the Python pages' JSON).
import postgres from "postgres";

export type Sql = postgres.Sql<Record<string, unknown>>;
export type Row = Record<string, any>;  // eslint-disable-line @typescript-eslint/no-explicit-any

// int8 (ids, counts) as JS numbers: every id and count here is far below 2^53
const types = { bigint: { to: 20, from: [20], parse: (x: string) => Number(x), serialize: (x: number) => String(x) } };

/** a pool: by default the Worker's per-request client; a long-running server passes its own size and idle time
 *  (yyc-mcp: mcp-edge/src/node/server.ts) */
// A planned primary move (a CloudNativePG switchover) cuts every connection and refuses new ones for a few seconds.
// A query caught by it runs again (every RETRY_GAP_MS for up to RETRY_FOR_MS), so a page or a yyc-mcp tool call waits
// instead of failing (Deyao, 2026-11-09: zero downtime). Safe: the site only reads, and the switchover fences writes
// first (news-stream/deploy/clusters/talos/db/switchover.sh locks every table EXCLUSIVE before the old primary stops),
// so yyc-mcp's own writes were waiting, not half done.
export const RETRY_FOR_MS = 30_000;
export const RETRY_GAP_MS = 200;
const LOST = new Set(["CONNECTION_CLOSED", "CONNECTION_ENDED", "CONNECTION_DESTROYED", "CONNECT_TIMEOUT", "ECONNREFUSED",
  "ECONNRESET", "EPIPE", "ETIMEDOUT", "EHOSTUNREACH"]);

/** the database away for a moment: a lost or refused connection (postgres.js's codes, SQLSTATE class 08, 57P01-03,
 *  25006), or Hyperdrive saying it can't reach the origin */
export function transient(e: unknown): boolean {
  const x = e as { code?: unknown; message?: unknown } | null;
  const code = String(x?.code ?? "");
  if (LOST.has(code) || /^08/.test(code) || /^57P0[123]$/.test(code) || code === "25006") return true;
  return /terminating connection|connection to the origin|failed to connect|could not connect|network connection lost/i
    .test(String(x?.message ?? ""));
}

async function again<T>(make: () => PromiseLike<T>): Promise<T> {
  const end = Date.now() + RETRY_FOR_MS;
  for (;;) {
    try {
      return await make();
    } catch (e) {
      if (!transient(e) || Date.now() >= end) throw e;
      await new Promise((r) => setTimeout(r, RETRY_GAP_MS));
    }
  }
}

/** `sql` whose queries (tagged templates and sql.unsafe) run again while the database is away; helpers such as
 *  sql(ids) pass through untouched. A query stays postgres.js's own (it nests in another query as a fragment, which
 *  postgres.js knows by `instanceof`; a stand-in object went in as a parameter and matched nothing); awaiting it runs
 *  `make` through `again`, so each try is a query made afresh (postgres.js runs one when it is awaited) */
export function retrying(sql: Sql): Sql {
  const lazy = <Q extends object>(q: Q, make: () => PromiseLike<unknown>): Q => {
    let run: Promise<unknown> | undefined;  // one run however many times it is awaited
    const go = () => (run ??= again(make));
    return Object.assign(q, {
      then: <A, B>(ok?: (v: unknown) => A | PromiseLike<A>, bad?: (e: unknown) => B | PromiseLike<B>) => go().then(ok, bad),
      catch: <B,>(bad: (e: unknown) => B | PromiseLike<B>) => go().catch(bad),
      finally: (f: () => void) => go().finally(f),
    });
  };
  return new Proxy(sql, {
    apply(target, self, args: unknown[]) {
      const first = args[0] as { raw?: unknown } | undefined;
      if (!(Array.isArray(first) && first.raw)) return Reflect.apply(target, self, args);  // a helper, not a query
      return lazy(Reflect.apply(target, self, args) as object, () => Reflect.apply(target, self, args) as PromiseLike<unknown>);
    },
    get(target, prop, recv) {
      if (prop === "unsafe") {
        return (...a: Parameters<Sql["unsafe"]>) =>
          lazy(target.unsafe(...a) as object, () => target.unsafe(...a) as unknown as PromiseLike<unknown>);
      }
      return Reflect.get(target, prop, recv);
    },
  }) as Sql;
}

export function connect(url: string, o: { max?: number; idle?: number; name?: string; write?: boolean } = {}): Sql {
  return retrying(postgres(url, {
    max: o.max ?? 5, fetch_types: false, prepare: true, idle_timeout: o.idle ?? 5, connect_timeout: 10, types,
    // read-only: the site never writes (Deyao, 2026-10-14); the role itself is read-only too (`write`: yyc-mcp's own
    // database mcp). Hyperdrive drops these startup parameters (SHOW statement_timeout gave 0, 2026-10-29): the roles
    // yyc_edge and yyc_readonly have their own (read only, statement_timeout 30s: deploy/db/postgres.yaml)
    connection: { ...(o.write ? {} : { default_transaction_read_only: true }), statement_timeout: 30000,
      application_name: o.name ?? "yyc-edge" },
  }) as unknown as Sql);
}

// A Worker may not use a socket opened by another request, so each request gets its own client (Hyperdrive keeps the
// real connections pooled next to the database, so this costs little). server.ts runs every request inside `perRequest`.
import { AsyncLocalStorage } from "node:async_hooks";

/** what one request keeps: its database client, and who is reading (server/reader.ts: asked once per request;
 *  signedIn once the answer is an approved account) */
interface RequestState { sql?: Sql; reader?: Promise<string | null | undefined>; signedIn?: boolean }
const store = new AsyncLocalStorage<RequestState>();
export const perRequest = <T,>(fn: () => T): T => store.run({}, fn);
export const requestState = (): RequestState | undefined => store.getStore();

/** this request's client */
export async function db(): Promise<Sql> {
  const { env } = await import("cloudflare:workers");
  const url = (env as unknown as { HYPERDRIVE: { connectionString: string } }).HYPERDRIVE.connectionString;
  const s = store.getStore();
  if (!s) return connect(url);
  return (s.sql ??= connect(url));
}
