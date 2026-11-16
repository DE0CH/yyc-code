// Node loader hooks for the label Worker's tests: the name-tag Worker's (`cloudflare:workers` stub, *.html as text),
// plus what wrangler's bundler does for this Worker: *.md as text, *.json as its value, *.wasm as a compiled module.
import { readFile } from "node:fs/promises";
export { resolve } from "../../../nametags/worker/test/hooks.mjs";
import { load as base } from "../../../nametags/worker/test/hooks.mjs";

export async function load(url, ctx, next) {
  if (url.endsWith(".md")) return { format: "module", source: `export default ${JSON.stringify(await readFile(new URL(url), "utf8"))};`, shortCircuit: true };
  if (url.endsWith(".json") && !ctx.importAttributes?.type) return { format: "module", source: `export default ${await readFile(new URL(url), "utf8")};`, shortCircuit: true };
  if (url.endsWith(".wasm")) return { format: "module", source: `import { readFileSync } from "node:fs"; export default new WebAssembly.Module(readFileSync(new URL(${JSON.stringify(url)})));`, shortCircuit: true };
  return base(url, ctx, next);
}
