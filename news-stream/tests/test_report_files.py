#!/usr/bin/env python3
"""The report files live in Postgres (table report_files), not on the volume: the edge app (web-app) serves them with
the cluster off the read path.

    TEST_DATABASE_URL=postgresql:///yyc_test python3 tests/test_report_files.py

1. reports.build stores every file it makes in report_files (Content-Type by extension) and writes nothing under
   $DATA_DIR/reports; a rebuild replaces that profile's day (a file it no longer makes is gone); daily() and
   run_profile() remove the build's temporary folder once it is stored; nothing is emailed (Deyao, 2026-10-23).
2. The edge app lists (/api/reports) and serves (/reports/<day>/<profile>/<file>) from the table (its reports.listing
   and reports.stored, run in Node by tests/edge_call.py): newest 60 days, profiles and files in name order, the
   stored Content-Type, nothing for a missing file or an extension reports.TYPES doesn't name. The flag (off on the
   main site) is tests/test_edge_ops.py's.
3. reports.py backfill stores an old reports folder once (files already stored stay as they are), keeping each file's
   time as `created`.
"""
import json, os, shutil, sys, tempfile, unittest, unittest.mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit("TEST_DATABASE_URL is not set")
os.environ.setdefault("FLAGS_FILE", os.path.join(HERE, "deploy", "apps", "flags", "flags.json"))
DATA = tempfile.mkdtemp(prefix="yyc-reports-test-")
os.environ["DATA_DIR"] = DATA
os.chdir(HERE)
import app, corpus, insights, reports  # noqa: E402
from edge_call import edge  # noqa: E402

PROFILE = {"id": "TESTRPT", "name": "測試", "title": "測試設定檔"}


def stored(day, pid, name):
    """(Content-Type, bytes) of a stored file, or None"""
    r = corpus.db().execute("SELECT ctype, body FROM report_files WHERE day = %s AND profile = %s AND name = %s",
                            (day, pid, name)).fetchone()
    return (r[0], bytes(r[1])) if r else None


def put(day, pid, files):
    d = tempfile.mkdtemp()
    try:
        for name, body in files.items():
            with open(os.path.join(d, name), "wb") as f:
                f.write(body)
        return reports.store(day, pid, d)
    finally:
        shutil.rmtree(d)


class Base(unittest.TestCase):
    def setUp(self):
        corpus.db().execute("DELETE FROM report_files")


class Build(Base):
    def test_a_day_without_posts_is_stored_not_written_to_the_volume(self):
        with unittest.mock.patch.object(reports.insights, "select", lambda *a, **k: []), \
                unittest.mock.patch.object(reports, "window", lambda pid, day: (None, None, [])):
            res = reports.build(PROFILE, "2026-10-01")
        try:
            self.assertEqual(res["files"], ["summary.json"])
            self.assertTrue(res["dir"].startswith(tempfile.gettempdir()))
            self.assertFalse(os.path.exists(os.path.join(DATA, "reports")))
            ctype, body = stored("2026-10-01", "TESTRPT", "summary.json")
            self.assertEqual(ctype, "application/json")
            self.assertEqual(json.loads(body)["note"], "當日沒有相關帖子，沒有產生報告。")
        finally:
            reports.done(res)
        self.assertFalse(os.path.exists(res["dir"]))

    def test_full_build_stores_every_file(self):
        def fake_build(p, day, out, use_ai):
            for n, b in (("format1.html", b"<p>1</p>"), ("format2.html", b"<p>2</p>"), ("report.pdf", b"%PDF-1.4"),
                         ("hot_topics.xlsx", b"PK1"), ("posts.xlsx", b"PK2"), ("posts.docx", b"PK3"), ("summary.json", b"{}")):
                with open(os.path.join(out, n), "wb") as f:
                    f.write(b)
            return {"dir": out, "files": sorted(os.listdir(out)), "summary": {"posts": 3}}
        with unittest.mock.patch.object(reports, "_build", fake_build):
            res = reports.build(PROFILE, "2026-10-02")
            reports.done(res)
        types = dict(corpus.db().execute("SELECT name, ctype FROM report_files WHERE day = '2026-10-02'").fetchall())
        self.assertEqual(types, {
            "format1.html": "text/html; charset=utf-8", "format2.html": "text/html; charset=utf-8",
            "report.pdf": "application/pdf", "summary.json": "application/json",
            "hot_topics.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "posts.xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "posts.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"})
        self.assertEqual(stored("2026-10-02", "TESTRPT", "report.pdf"), ("application/pdf", b"%PDF-1.4"))

    def test_rebuild_replaces_the_day(self):
        put("2026-10-03", "TESTRPT", {"format1.html": b"a", "report.pdf": b"b", "summary.json": b"{}"})
        put("2026-10-03", "OTHER", {"summary.json": b"{}"})
        put("2026-10-03", "TESTRPT", {"summary.json": b'{"posts":0}'})
        self.assertIsNone(stored("2026-10-03", "TESTRPT", "report.pdf"))
        self.assertEqual(stored("2026-10-03", "TESTRPT", "summary.json")[1], b'{"posts":0}')
        self.assertIsNotNone(stored("2026-10-03", "OTHER", "summary.json"))

    def test_failed_build_leaves_no_folder_and_keeps_the_old_files(self):
        put("2026-10-04", "TESTRPT", {"report.pdf": b"old"})
        seen = {}

        def boom(p, day, out, use_ai):
            seen["dir"] = out
            raise RuntimeError("chrome print failed")
        with unittest.mock.patch.object(reports, "_build", boom):
            with self.assertRaises(RuntimeError):
                reports.build(PROFILE, "2026-10-04")
        self.assertFalse(os.path.exists(seen["dir"]))
        self.assertEqual(stored("2026-10-04", "TESTRPT", "report.pdf")[1], b"old")

    def test_daily_stores_the_build_then_removes_it(self):
        dirs = []

        def fake_build(p, day, out, use_ai):
            with open(os.path.join(out, "format1.html"), "w") as f:
                f.write("<p>x</p>")
            dirs.append(out)
            return {"dir": out, "files": ["format1.html"], "summary": {"posts": 1}}
        with unittest.mock.patch.object(reports, "_build", fake_build), \
                unittest.mock.patch.object(reports.insights, "profiles", lambda: [PROFILE]), \
                unittest.mock.patch.object(reports.insights, "profile", lambda pid: PROFILE), \
                unittest.mock.patch.object(reports, "themes_due", lambda pid: False), \
                unittest.mock.patch.object(reports.ai, "spend", lambda: {}):
            reports.daily("2026-10-05")
            r = reports.run_profile("TESTRPT", "2026-10-06")
        self.assertEqual(len(dirs), 2)
        for d in dirs:
            self.assertFalse(os.path.exists(d))
        self.assertEqual(r["files"], ["/reports/2026-10-06/TESTRPT/format1.html"])
        self.assertFalse(os.path.exists(os.path.join(DATA, "reports")))


class Serve(Base):
    def test_listing_order_and_limit(self):
        put("2026-09-30", "B", {"summary.json": b"{}", "format1.html": b"x"})
        put("2026-09-30", "A", {"report.pdf": b"p", "Z.txt": b"z", "a.txt": b"a"})
        put("2026-10-01", "CEHK", {"summary.json": b"{}"})
        self.assertEqual(edge("reports.listing"), [
            {"day": "2026-10-01", "profile": "CEHK", "files": ["/reports/2026-10-01/CEHK/summary.json"]},
            {"day": "2026-09-30", "profile": "A", "files": ["/reports/2026-09-30/A/Z.txt", "/reports/2026-09-30/A/a.txt",
                                                            "/reports/2026-09-30/A/report.pdf"]},
            {"day": "2026-09-30", "profile": "B", "files": ["/reports/2026-09-30/B/format1.html",
                                                            "/reports/2026-09-30/B/summary.json"]}])
        self.assertEqual([x["day"] for x in edge("reports.listing", 1)], ["2026-10-01"])
        rows = [(f"2026-08-{d:02d}", "P", "summary.json", "application/json", b"{}") for d in range(1, 32)]
        rows += [(f"2026-07-{d:02d}", "P", "summary.json", "application/json", b"{}") for d in range(1, 32)]
        corpus.db().executemany("INSERT INTO report_files (day, profile, name, ctype, body) VALUES (%s, %s, %s, %s, %s)", rows)
        days = [x["day"] for x in edge("reports.listing")]
        self.assertEqual(len(set(days)), 60)
        self.assertEqual(days[0], "2026-10-01")
        self.assertNotIn("2026-07-01", days)

    def test_site_serves_from_the_table(self):
        import hashlib
        put("2026-10-01", "CEHK", {"report.pdf": b"%PDF-x", "format1.html": "<p>報告</p>".encode(), "notes.bin": b"?"})
        got = edge("reports.storedDigests", [["2026-10-01", "CEHK", n] for n in
                                             ("report.pdf", "format1.html", "notes.bin", "none.pdf")])
        sha = lambda b: hashlib.sha256(b).hexdigest()
        self.assertEqual(got, [{"ctype": "application/pdf", "sha": sha(b"%PDF-x"), "len": 6},
                               {"ctype": "text/html; charset=utf-8", "sha": sha("<p>報告</p>".encode()), "len": 13},
                               None, None])


class Backfill(Base):
    def test_backfill_once(self):
        root = tempfile.mkdtemp()
        try:
            for day, pid, name, body in (("2026-09-01", "CEHK", "report.pdf", b"old pdf"), ("2026-09-01", "CEHK", "summary.json", b"{}"),
                                         ("2026-09-02", "FSHK", "posts.docx", b"docx"), ("notaday", "X", "a.pdf", b"x"),
                                         ("2026-09-02", "bad id!", "a.pdf", b"x")):
                os.makedirs(os.path.join(root, day, pid), exist_ok=True)
                with open(os.path.join(root, day, pid, name), "wb") as f:
                    f.write(body)
            os.utime(os.path.join(root, "2026-09-01", "CEHK", "report.pdf"), (1757000000, 1757000000))
            put("2026-09-02", "FSHK", {"posts.docx": b"newer build"})
            got = reports.backfill(root)
            self.assertEqual(got, [("2026-09-01", "CEHK", "report.pdf"), ("2026-09-01", "CEHK", "summary.json")])
            self.assertEqual(stored("2026-09-02", "FSHK", "posts.docx")[1], b"newer build")
            self.assertEqual(stored("2026-09-01", "CEHK", "report.pdf"), ("application/pdf", b"old pdf"))
            created = corpus.db().execute("SELECT extract(epoch FROM created) FROM report_files WHERE name = 'report.pdf'").fetchone()[0]
            self.assertEqual(int(created), 1757000000)
            self.assertEqual(reports.backfill(root), [])  # a second run stores nothing
            self.assertEqual(reports.backfill(os.path.join(root, "nope")), [])
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=1)
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
