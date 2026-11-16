// 搜尋's server functions (server/search/search.ts): the search and the filters' media list. Public data, behind the
// `search` flag of the reader's site (server/gates.ts): off → 404 {"error": "not found"}.
import { createServerFn, createServerOnlyFn } from "@tanstack/react-start";
import { getRequestHeader, setResponseHeader, setResponseStatus } from "@tanstack/react-start/server";
import { db } from "./server/db";
import { flagOn } from "./server/gates";
import { errorJson, meta, run, type QS } from "./server/search/search";
import type { Json } from "./fns";

const J = <T,>(v: T) => v as unknown as Json;
const noStore = createServerOnlyFn(() => setResponseHeader("Cache-Control", "private, no-store"));
const siteHost = createServerOnlyFn(() => (getRequestHeader("x-forwarded-host") || getRequestHeader("host") || "").split(",")[0].trim());
const off = createServerOnlyFn(async () => !(await flagOn("search", siteHost())));

/** the query string: every non-blank value of each key */
const validQS = (d: unknown): QS => {
  const out: QS = {};
  if (d && typeof d === "object") for (const [k, v] of Object.entries(d as Record<string, unknown>)) {
    const xs = (Array.isArray(v) ? v : [v]).filter((x): x is string => typeof x === "string" && x !== "");
    if (xs.length) out[k] = xs;
  }
  return out;
};

/** /api/search: the results, or {status, error} for a bad query */
export const fetchSearch = createServerFn({ method: "GET" })
  .validator(validQS)
  .handler(async ({ data }) => {
    noStore();
    if (await off()) { setResponseStatus(404); return J({ status: 404, error: "not found" }); }
    try { return J(await run(await db(), data)); }
    catch (e) { const r = errorJson(e); setResponseStatus(r.status); return J(r); }
  });

/** /api/search/meta: the media types and outlets to filter by */
export const fetchSearchMeta = createServerFn({ method: "GET" })
  .handler(async () => {
    noStore();
    if (await off()) { setResponseStatus(404); return J({ status: 404, error: "not found" }); }
    return J(await meta(await db()));
  });
