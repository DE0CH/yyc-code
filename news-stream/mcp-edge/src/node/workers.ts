// What `cloudflare:workers` gives the code yyc-mcp shares (the site's searchtext asks for env.SEARCH_CACHE and
// waitUntil): esbuild aliases the module here.
// No SEARCH_CACHE: searchtext keeps its caches in the process. waitUntil: work the server drains before it exits.
import { track } from "./drain";

export const env: Record<string, unknown> = {};

export const waitUntil = (p: Promise<unknown>) => track(p);
