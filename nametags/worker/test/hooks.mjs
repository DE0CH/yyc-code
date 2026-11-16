// Node loader hooks for the tests: `cloudflare:workers` → a stub so the Durable Object class loads under plain Node,
// and *.html → a module whose default export is the file's text (wrangler's "Text" rule does the same in the bundle).
import { readFile } from "node:fs/promises";

export async function resolve(spec, ctx, next) {
  if (spec === "cloudflare:workers") return { url: new URL("./cf-stub.mjs", import.meta.url).href, shortCircuit: true };
  return next(spec, ctx);
}
export async function load(url, ctx, next) {
  if (url.endsWith(".html")) return { format: "module", source: `export default ${JSON.stringify(await readFile(new URL(url), "utf8"))};`, shortCircuit: true };
  return next(url, ctx);
}
