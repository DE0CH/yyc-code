// The SSRF guard (../src/node/egress.ts): which addresses a client-metadata fetch may reach.
//   npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { guardEgress, isPublic } from "../src/node/egress.ts";

test("private, loopback, link-local, CGNAT and cluster addresses are refused", () => {
  for (const ip of ["10.0.0.2", "10.244.3.7", "10.45.0.10", "127.0.0.1", "169.254.169.254", "172.16.5.4", "192.168.1.1",
    "100.64.0.1", "0.0.0.0", "::1", "::", "fd00::1", "fe80::1", "::ffff:10.0.0.1", "::ffff:127.0.0.1", "64:ff9b::a00:1"]) {
    assert.equal(isPublic(ip), false, ip);
  }
});

test("public addresses pass", () => {
  for (const ip of ["8.8.8.8", "1.1.1.1", "142.250.185.78", "2a01:4f8:c17:1::1", "::ffff:8.8.8.8"]) assert.equal(isPublic(ip), true, ip);
});

test("a fetch to a private literal or a name resolving to one fails before connecting", async () => {
  guardEgress();
  await assert.rejects(fetch("https://10.0.0.2/"), /non-public/);
  await assert.rejects(fetch("http://[::1]:8080/"), /non-public/);
  await assert.rejects(fetch("http://localhost:8080/"), (e: Error) => /no public address/.test(String((e as { cause?: unknown }).cause ?? e)));
});
