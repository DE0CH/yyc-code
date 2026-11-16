// node --import: the loader hooks in hooks.mjs (`cloudflare:workers` stub, *.html as text).
import { register } from "node:module";
register("./hooks.mjs", import.meta.url);
