// The site's data API: TanStack Start server functions. Each runs on the Worker (next to the database) and reads
// Postgres through Hyperdrive; the pages call them from route loaders (server-rendered, then dehydrated into the page)
// and from TanStack Query in the browser. Public data only: what is for approved accounts is under /app (Access).
import { createServerFn, createServerOnlyFn } from "@tanstack/react-start";
import { getRequestHeader, setResponseHeader, setResponseStatus } from "@tanstack/react-start/server";
import { db } from "./server/db";
import * as feedData from "./server/feed";
import * as itemData from "./server/item";
import * as site from "./server/site";

/** what the data functions return: plain JSON (the pages' types live in api.ts) */
export type Json = string | number | boolean | null | Json[] | { [k: string]: Json };
const J = <T,>(v: T) => v as unknown as Json;

const noStore = createServerOnlyFn(() => setResponseHeader("Cache-Control", "private, no-store"));
/** the site the reader is on (yyc-edge passes the original Host as X-Forwarded-Host) */
const siteHost = createServerOnlyFn(() => (getRequestHeader("x-forwarded-host") || getRequestHeader("host") || "").split(",")[0].trim());

export const fetchFeed = createServerFn({ method: "GET" })
  .validator((d: { date?: string | null }) => ({ date: d?.date && /^\d{4}-\d{2}-\d{2}$/.test(d.date) ? d.date : null }))
  .handler(async ({ data }) => { noStore(); return J(await feedData.feed(await db(), data.date)); });

export const fetchProfiles = createServerFn({ method: "GET" })
  .handler(async () => { noStore(); return J(await site.profiles(await db())); });

export const fetchFlags = createServerFn({ method: "GET" })
  .handler(async () => { noStore(); return J(await site.flags(await db(), siteHost())); });

/** one post for the item page (?id= or ?url=); null when the system has no such post */
export const fetchItem = createServerFn({ method: "GET" })
  .validator((d: { id?: number | string | null; url?: string | null }) => d)
  .handler(async ({ data }) => {
    noStore();
    const sql = await db();
    const pid = await itemData.find(sql, data);
    if (!pid) { setResponseStatus(404); return null; }
    return J(await itemData.view(sql, pid));
  });

/** 同一事件的其他報道 for the item page: slower than the rest, so the page shows without waiting for it */
export const fetchItemCoverage = createServerFn({ method: "GET" })
  .validator((d: { id: number }) => ({ id: Number(d.id) }))
  .handler(async ({ data }) => { noStore(); return J(await itemData.itemCoverage(await db(), data.id)); });

/** 報告 (/api/reports): the stored report files of the newest 60 days; null (404) when the reports flag is off for
 *  this site (server/gates.ts) */
export const fetchReports = createServerFn({ method: "GET" })
  .handler(async () => {
    noStore();
    const { flagOn } = await import("./server/gates");
    if (!(await flagOn("reports", siteHost()))) { setResponseStatus(404); return null; }
    const { listing } = await import("./server/reports");
    return J({ reports: await listing(await db()) });
  });
