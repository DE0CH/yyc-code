// yyc-mcp on Node (Deployment yyc-mcp, deploy/apps/yyc-mcp.yaml): the same fetch handler as the Worker had, served by
// node:http on PORT (8080). Settings from the environment (env.ts); the two pools to Postgres stay open for the life of
// the process. On SIGTERM (after the preStop sleep that takes the pod off the Service) it stops accepting, finishes the
// requests in flight and the work they left (audit writes), then closes the pools.
import http from "node:http";
import { connect } from "../../../web-app/src/server/db";
import { loadCatalogue } from "../catalogue";
import type { Env } from "../env";
import handler from "../index";
import { Registry, SCHEMA } from "../registry";
import { drain, track } from "./drain";
import { guardEgress } from "./egress";

const VARS = ["BASE_URL", "CATALOGUE", "RATE_PER_MIN", "OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET",
  "CONSENT_SECRET"] as const;
const OPTIONAL = ["SERVICE_TOKENS", "WISERS_API", "WISERS_INTERNAL_TOKEN"] as const;
const MAX_BODY = 1 << 20;

/** the settings, the catalogue and the pools (and how to close them); the database mcp's tables are made when missing */
export async function makeEnv(): Promise<{ env: Env; close(): Promise<void> }> {
  const missing = [...VARS, "DATABASE_URL", "MCP_DATABASE_URL"].filter(k => !process.env[k]);
  if (missing.length) throw new Error(`missing settings: ${missing.join(", ")}`);
  loadCatalogue({ CATALOGUE: process.env.CATALOGUE! });
  const app = connect(process.env.DATABASE_URL!, { max: 10, idle: 300, name: "yyc-mcp" });
  const mcp = connect(process.env.MCP_DATABASE_URL!, { max: 4, idle: 300, name: "yyc-mcp", write: true });
  await mcp.unsafe(SCHEMA);
  const env = Object.fromEntries([...VARS, ...OPTIONAL].map(k => [k, process.env[k]])) as unknown as Env;
  env.DB = app;
  env.REGISTRY = new Registry(env, mcp);
  return { env, close: async () => { await Promise.all([app.end({ timeout: 5 }), mcp.end({ timeout: 5 })]); } };
}

const ctx = { waitUntil: track, passThroughOnException() {} };

async function serve(env: Env, req: http.IncomingMessage, res: http.ServerResponse) {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const c of req) {
    size += (c as Buffer).length;
    if (size > MAX_BODY) { res.writeHead(413).end(); return; }
    chunks.push(c as Buffer);
  }
  // the public origin, whatever the Host (cloudflared keeps mcp.yyc-internal.com; YYC AI calls the Service name): the
  // OAuth resource checks and every link the pages make are about BASE_URL
  const url = new URL(req.url || "/", env.BASE_URL);
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) {
    if (v !== undefined && k !== "host") for (const x of Array.isArray(v) ? v : [v]) headers.append(k, x);
  }
  const body = chunks.length && req.method !== "GET" && req.method !== "HEAD" ? Buffer.concat(chunks) : undefined;
  const r = await handler.fetch(new Request(url, { method: req.method, headers, body }), env, ctx as never);
  const out: Record<string, string | string[]> = {};
  for (const [k, v] of r.headers) if (k !== "set-cookie") out[k] = v;
  const cookies = r.headers.getSetCookie();
  if (cookies.length) out["set-cookie"] = cookies;
  res.writeHead(r.status, out);
  if (r.body && req.method !== "HEAD") for await (const c of r.body as unknown as AsyncIterable<Uint8Array>) res.write(c);
  res.end();
}

export async function main() {
  guardEgress();
  const { env, close } = await makeEnv();
  const server = http.createServer((req, res) => {
    serve(env, req, res).catch(e => {
      console.error("request failed", e);
      if (!res.headersSent) res.writeHead(500, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "server error" }));
    });
  });
  server.keepAliveTimeout = 65_000;  // longer than cloudflared's idle pool timeout: it closes first, never mid-request
  server.listen(Number(process.env.PORT || 8080), () => console.log(JSON.stringify({ listening: Number(process.env.PORT || 8080) })));
  process.once("SIGTERM", () => {
    server.close(async () => {
      await drain(20_000);
      await close();
      process.exit(0);
    });
    server.closeIdleConnections();
  });
}
