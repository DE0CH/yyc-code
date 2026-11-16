// The YYC 新聞監察 site: TanStack Start (React, server-rendered on a Cloudflare Worker, hydrated in the browser), built
// with Vite and Cloudflare's plugin. `npm run dev` runs the Worker locally (workerd) against the Postgres in
// CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE; `npm run build` writes dist/ for `wrangler deploy`.
import { defineConfig } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import { cloudflare } from "@cloudflare/vite-plugin";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// the builds id: pages compare it with /version to notice a newer deploy (src/lib/stale.ts)
const BUILD_ID = process.env.GITHUB_SHA?.slice(0, 12) || String(Date.now());

export default defineConfig({
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  plugins: [
    cloudflare({ viteEnvironment: { name: "ssr" } }),
    tanstackStart({ srcDirectory: "src" }),
    viteReact(),
    tailwindcss(),
  ],
  server: { host: "127.0.0.1", port: 5173 },
  preview: { host: "127.0.0.1", port: 4173 },
  build: { target: "es2022", sourcemap: false },
});
