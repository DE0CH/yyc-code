// What a REST caller (YYC AI's agent, through tools) is told when a tool fails on the server (../src/failure.ts).
//   npm test
import { test } from "node:test";
import assert from "node:assert/strict";
import { failure } from "../src/failure.ts";

test("a query Postgres stopped at the statement timeout says so, and what finishes sooner", () => {
  const e = Object.assign(new Error("canceling statement due to statement timeout"), { code: "57014" });
  assert.equal(failure(e), "the search ran past the database's 30 s time limit and was stopped; fewer days or more specific words finish sooner");
});

test("any other failure stays unnamed (no internals reach the caller)", () => {
  assert.equal(failure(new Error("connect ECONNREFUSED 10.0.0.5:5432")), "the tool failed on the server");
  assert.equal(failure("x"), "the tool failed on the server");
});
