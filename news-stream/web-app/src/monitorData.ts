// Deyao's monitor data (/app/monitor/data). The page's first render reads it on the server, inside the request
// server.ts already let through for Deyao's Access identity (checked again here); the browser then reads
// /app/monitor/data, the only place Cloudflare Access sends his token (server functions live outside /app).
import { createIsomorphicFn } from "@tanstack/react-start";
import { queryOptions } from "@tanstack/react-query";

export const getMonitor = createIsomorphicFn()
  .server(async () => {
    const { getRequest } = await import("@tanstack/react-start/server");
    const { accessUser, ADMIN_EMAIL } = await import("./server/access");
    if ((await accessUser(getRequest())) !== ADMIN_EMAIL) return null;
    const { db } = await import("./server/db");
    const { published } = await import("./server/monitor");
    return published(await db()) as Promise<Record<string, unknown>>;
  })
  .client(async () => {
    const r = await fetch("/app/monitor/data", { cache: "no-store", credentials: "same-origin" });
    const j = (await r.json().catch(() => ({ error: "HTTP " + r.status }))) as Record<string, unknown> & { error?: string };
    if (!r.ok) throw new Error(j.error || "HTTP " + r.status);
    return j;
  });

export const monitorQuery = queryOptions({ queryKey: ["monitor"] as const, queryFn: () => getMonitor(), staleTime: 30_000, retry: false });
