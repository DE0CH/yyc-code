// node --import: loader hooks (test/hooks.mjs: `cloudflare:workers` stub, *.html and *.md as text, *.json, *.wasm).
import { register } from "node:module";
register("./hooks.mjs", import.meta.url);
