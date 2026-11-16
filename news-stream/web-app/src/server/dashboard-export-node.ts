// Node only (scripts/call.ts, the parity tests): an export written to a temporary file → {name, disposition, path}
import { writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Sql } from "./db";
import { exportXlsx } from "./dashboard-export";
import { attachment } from "./xlsx";
import type { Q } from "./dashboard";

export async function exportFile(sql: Sql, kind: string, q: Q) {
  const [name, data] = await exportXlsx(sql, kind, q);
  const path = join(tmpdir(), `edge-export-${process.pid}-${name}`);
  writeFileSync(path, data);
  return { name, disposition: attachment(name), path };
}
