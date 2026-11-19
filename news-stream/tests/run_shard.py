#!/usr/bin/env python3
"""Run one shard of news-stream/tests/ (CI: .github/workflows/news-stream-tests.yml), each file on its own fresh
Postgres database, one after another (two files on one database deadlock on the DROP SCHEMA at import).

    python3 tests/run_shard.py SHARD TOTAL [--out DIR]     # the files test_*.py sorted, every TOTAL-th from SHARD
    python3 tests/run_shard.py --files tests/test_watch.py tests/test_query_pg.py

Needs a local Postgres where the current user may create databases (TEST_DATABASE_URL and DATABASE_URL = the file's
postgresql:///<db>, the Unix socket: code that reads DATABASE_URL itself meets the throwaway database too) and the login claude:dev over TCP for the tests that call the edge app (tests/edge_call.py). Each file's output
goes to DIR/<name>.log; the summary lists every failing file with the tail of its output. Exit 1 when any file failed.
"""
import argparse, glob, os, subprocess, sys, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = int(os.environ.get("TEST_FILE_TIMEOUT", 900))


def run(path, out):
    name = os.path.basename(path)[:-3]
    db = "t_" + name
    subprocess.run(["dropdb", "--if-exists", db], check=True, capture_output=True)
    subprocess.run(["createdb", db], check=True)
    t0 = time.time()
    with open(os.path.join(out, name + ".log"), "w") as f:
        try:
            code = subprocess.run([sys.executable, path], cwd=HERE, stdout=f, stderr=subprocess.STDOUT, timeout=TIMEOUT,
                                  env=dict(os.environ, TEST_DATABASE_URL=f"postgresql:///{db}",
                                           DATABASE_URL=f"postgresql:///{db}",
                                           # the tests that default to a developer's named copy of real data take
                                           # the file's own database too (test_edge_ops, test_mcp_edge)
                                           EDGE_DB=db, MCP_DB=db)).returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    subprocess.run(["dropdb", "--if-exists", db], capture_output=True)
    return name, code, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("shard", nargs="?", type=int)
    ap.add_argument("total", nargs="?", type=int)
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--out", default=os.path.join(HERE, "test-logs"))
    a = ap.parse_args()
    files = a.files or sorted(glob.glob(os.path.join(HERE, "tests", "test_*.py")))[a.shard::a.total]
    os.makedirs(a.out, exist_ok=True)
    failed = []
    for path in files:
        name, code, secs = run(path, a.out)
        print(f"{'ok  ' if code == 0 else 'FAIL'} {name} ({secs:.0f} s{'' if code == 0 else f', exit {code}'})", flush=True)
        if code != 0:
            failed.append(name)
    for name in failed:
        with open(os.path.join(a.out, name + ".log")) as f:
            tail = f.read()[-4000:]
        print(f"\n::group::{name} (failed)\n{tail}\n::endgroup::")
    print(f"\n{len(files) - len(failed)}/{len(files)} files passed" + (f"; failed: {' '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
