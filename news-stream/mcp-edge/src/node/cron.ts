// dist/cron.js: CronJob mcp-cleanup, daily at 03:17 UTC: the rate counters, calls older than a year, expired OAuth keys,
// and the OAuth provider's own purge of expired grants and tokens
import handler from "../index";
import { makeEnv } from "./server";

const { env, close } = await makeEnv();
try {
  await handler.scheduled(env);
} finally { await close(); }
