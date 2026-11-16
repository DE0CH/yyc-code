// dist/server.js: the server (Deployment yyc-mcp)
import { main } from "./server";

main().catch(e => { console.error(e); process.exit(1); });
