#!/usr/bin/env python3
"""The edge app's database client (web-app/src/server/db.ts `connect`, through `retrying`): a query built from smaller
ones (a tagged template or sql.unsafe inside another) runs them as SQL, not as parameters, and gives the rows the same
query typed out in full gives. yyc-mcp's posts_search without the search dictionary builds its WHERE that way; when
`retrying` wrapped each query in an object of its own, postgres.js took the inner ones for values and the search
matched nothing.

    python3 tests/test_edge_db.py        # TEST_DATABASE_URL (run_shard.py), reached over TCP as claude:dev
"""
import json, os, subprocess, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "web-app")
NAME = (os.environ.get("TEST_DATABASE_URL") or "postgresql:///yyc_test_edge_db").rsplit("/", 1)[1]
URL = f"postgres://claude:dev@127.0.0.1:5432/{NAME}"

PROG = """
import { connect } from "./src/server/db.ts";
const sql = connect(process.env.URL);
const like = ["%b%"];
const thread = (all) => all ? sql`TRUE` : sql`t.thread IS NULL`;
const cond = sql`t.day >= ${"2026-01-02"} AND (${thread(false)})
  AND t.title ILIKE ANY(ARRAY(SELECT jsonb_array_elements_text(${JSON.stringify(like)}::text::jsonb))) ${sql``}`;
const rows = (q) => q.then(r => r.map(x => x.id));
const out = {
  nested: await rows(sql`SELECT id FROM t WHERE ${cond} ORDER BY id`),
  unsafe: await rows(sql`SELECT id FROM t WHERE ${sql.unsafe("t.thread IS NULL")} ORDER BY id`),
  flat: await rows(sql`SELECT id FROM t WHERE t.day >= '2026-01-02' AND t.thread IS NULL AND t.title ILIKE '%b%' ORDER BY id`),
  twice: await (async () => { const q = sql`SELECT count(*) AS n FROM t`; return [(await q)[0].n, (await q)[0].n]; })(),
};
console.log(JSON.stringify(out));
await sql.end();
"""


class EdgeDb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(["psql", URL, "-qc", """DROP TABLE IF EXISTS t;
            CREATE TABLE t (id bigint PRIMARY KEY, day text, thread text, title text);
            INSERT INTO t VALUES (1, '2026-01-01', NULL, 'abc'), (2, '2026-01-03', NULL, 'abc'),
                                 (3, '2026-01-03', 'x', 'abc'), (4, '2026-01-03', NULL, 'xyz'), (5, '2026-01-04', NULL, 'b')"""],
                       check=True)
        r = subprocess.run(["npx", "tsx", "--input-type=module", "-e", PROG], cwd=APP, capture_output=True, text=True,
                           timeout=120, env=dict(os.environ, URL=URL))
        assert r.returncode == 0, r.stderr[-3000:]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_inner_queries_are_sql(self):
        self.assertEqual(self.out["nested"], [2, 5])
        self.assertEqual(self.out["nested"], self.out["flat"])
        self.assertEqual(self.out["unsafe"], [1, 2, 4, 5])

    def test_a_query_awaited_twice_runs_once(self):
        self.assertEqual(self.out["twice"], [5, 5])


if __name__ == "__main__":
    unittest.main(verbosity=2)
