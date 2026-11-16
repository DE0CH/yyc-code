// After `vite build`: dist/client/LICENSES.txt, the licence text of every package the bundles can contain (the non-dev
// packages in package-lock.json), so the notices travel with the minified copies (as agent-ui/licenses.mjs did for the
// AI 助理's assistant-ui build). Served at /LICENSES.txt with the app's other static files.
import { readFileSync, readdirSync, writeFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const lock = JSON.parse(readFileSync("package-lock.json", "utf8"));
const out = [];
for (const [path, p] of Object.entries(lock.packages)) {
  if (!path.startsWith("node_modules/") || p.dev) continue;
  const name = path.replace(/^.*node_modules\//, "");
  const file = existsSync(path) && readdirSync(path).find(f => /^(licen[cs]e|copying)(\.(md|txt))?$/i.test(f));
  out.push(`==== ${name}@${p.version} (${p.license || "see text"}) ====\n` + (file ? readFileSync(join(path, file), "utf8").trim() : "(no licence file in the package)") + "\n");
}
writeFileSync("dist/client/LICENSES.txt", out.join("\n"));
console.log(`LICENSES.txt: ${out.length} packages`);
