// Run yyc-mcp tools in Node against DATABASE_URL and print their answers as JSON (the tools' tests read them):
//   tsx scripts/call.ts < [[tool, args], …]  (JSON on stdin) → [answer | {error}, …]
import { connect } from "../../web-app/src/server/db";
import { runTool } from "../src/tools";

const sql = connect(process.env.DATABASE_URL!);
try {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  const out = [];
  for (const [name, args] of JSON.parse(input) as [string, Record<string, unknown>][]) {
    try { out.push(await runTool(sql, name, args)); } catch (e) { out.push({ error: String((e as Error).message) }); }
  }
  process.stdout.write(JSON.stringify(out));
} finally {
  await sql.end({ timeout: 1 });
}
