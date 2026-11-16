// Server-Timing on every MCP / REST answer (2026-10-29: a call took ~1 s while the search itself took ~0.2 s): how long
// the token check, the admission (catalogue + rate limit, Registry.admit), the tool and its parts took, per request (index.ts withTiming adds the header)
import { AsyncLocalStorage } from "node:async_hooks";

export const timing = new AsyncLocalStorage<[string, number][]>();

export async function timed<T>(name: string, f: () => Promise<T>): Promise<T> {
  const t = Date.now();
  try { return await f(); } finally { timing.getStore()?.push([name, Date.now() - t]); }
}
