// catalogue.ts: who may use the server is the access catalogue's users ("@domain" = everyone there); admins must also
// be users; a broken re-read keeps the last good copy.
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { isAdmin, isUser, loadCatalogue } from "../src/catalogue.ts";

const file = join(mkdtempSync(join(tmpdir(), "cat-")), "config.json");
const env = { CATALOGUE: file };

test("users, domains and admins", () => {
  writeFileSync(file, JSON.stringify({ users: ["@yyc.hk", "Staff@Gmail.com"], admins: ["boss@gmail.com", "staff@gmail.com"] }));
  loadCatalogue(env);
  assert.ok(isUser(env, "anyone@yyc.hk"));
  assert.ok(isUser(env, "STAFF@gmail.com"));
  assert.ok(!isUser(env, "x@notyyc.hk"));
  assert.ok(!isUser(env, "x@sub.yyc.hk"));
  assert.ok(!isUser(env, "yyc.hk"));
  assert.ok(isAdmin(env, "staff@gmail.com"));
  assert.ok(!isAdmin(env, "boss@gmail.com"), "an admin outside users is no one");
});

// the real catalogue is people's addresses, so it is never a file here (the store's YYC_CATALOGUE_JSON, mounted from
// Secret yyc-catalogue); this sample has its shape and is what the image's smoke test mounts (mcp-image.yml)
const SAMPLE = join(import.meta.dirname, "catalogue.sample.json");
test("the sample catalogue parses", () => {
  const sample = { CATALOGUE: SAMPLE };
  loadCatalogue(sample);
  assert.ok(isUser(sample, "someone@example.com"));
  assert.ok(isUser(sample, "tony.leung@example.org"));
  assert.ok(isAdmin(sample, "michelle.yeoh@example.com"));
  assert.ok(!isAdmin(sample, "tony.leung@example.org"));
});

test("a missing file fails at start-up", () => {
  assert.throws(() => loadCatalogue({ CATALOGUE: file + ".missing" }), /unreadable/);
});
