// Run one server data function in Node against DATABASE_URL and print its JSON (the parity tests compare it with
// the Python pages' JSON): tsx scripts/call.ts <module>.<function> '<json args array>'
// Many calls in one process: tsx scripts/call.ts --batch < [[name, args], …] (JSON on stdin) → [result, …]
import { connect } from "../src/server/db";

const [name, args = "[]"] = process.argv.slice(2);
const sql = connect(process.env.DATABASE_URL!);

async function call(name: string, args: unknown[]) {
  const [mod, fn] = name.split(".");
  const m = await import(`../src/server/${mod}.ts`);
  return m[fn](sql, ...args);
}

try {
  if (name === "--batch") {
    let input = "";
    for await (const chunk of process.stdin) input += chunk;
    const out = [];
    for (const [n, a] of JSON.parse(input) as [string, unknown[]][]) out.push(await call(n, a));
    process.stdout.write(JSON.stringify(out));
  } else {
    process.stdout.write(JSON.stringify(await call(name, JSON.parse(args))));
  }
} finally {
  await sql.end({ timeout: 1 });
}
