// The server and the cleanup job as two self-contained files in dist/ (the image carries no node_modules). The site's
// modules it shares (../web-app/src/server) come in as source; `cloudflare:workers` is src/node/workers.ts.
import { build } from "esbuild";

await build({
  entryPoints: { server: "src/node/main.ts", cron: "src/node/cron.ts" },
  outdir: "dist", bundle: true, platform: "node", target: "node22", format: "esm", sourcemap: true,
  // postgres from this package's node_modules for the site's modules too (the image installs only mcp-edge's)
  alias: { "cloudflare:workers": "./src/node/workers.ts", postgres: "./node_modules/postgres/src/index.js" },
  // CommonJS dependencies bundled into ESM still call require() for node built-ins
  banner: { js: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);" },
  logLevel: "warning",
});
