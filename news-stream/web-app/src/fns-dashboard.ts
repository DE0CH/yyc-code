// 數據分析's server functions (posts, hot_topics, topic, themes, persona, ai/daily, ai/context): public
// data read on the Worker. An error the page shows (no such profile, bad dates, no such topic) comes back as
// {error, status}, which api-dashboard.ts turns into an HttpError.
import { createServerFn, createServerOnlyFn } from "@tanstack/react-start";
import { setResponseHeader } from "@tanstack/react-start/server";
import { db } from "./server/db";
import { ApiError, ENDPOINTS, type Endpoint, type Q } from "./server/dashboard";
import type { Json } from "./fns";

const noStore = createServerOnlyFn(() => setResponseHeader("Cache-Control", "private, no-store"));

export const fetchDashboard = createServerFn({ method: "GET" })
  .validator((d: { name: string; q: Record<string, unknown> }) => {
    if (!d || !(d.name in ENDPOINTS)) throw new Error("unknown endpoint");
    const q: Q = {};
    for (const [k, v] of Object.entries(d.q || {})) if (typeof v === "string" && v !== "") q[k] = v.slice(0, 2000);
    return { name: d.name as Endpoint, q };
  })
  .handler(async ({ data }) => {
    noStore();
    try {
      return (await ENDPOINTS[data.name](await db(), data.q)) as unknown as Json;
    } catch (e) {
      if (e instanceof ApiError) return { error: e.message, status: e.status } as Json;
      throw e;
    }
  });
