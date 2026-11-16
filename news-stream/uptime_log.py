#!/usr/bin/env python3
"""infra/uptime-probe.sh's failures, kept in Postgres (kv "uptime:failures": "<time UTC> <path> <HTTP status> <seconds>"
lines, the last 90 days) instead of a file on a volume (Deyao, 2026-11-08: state in the application, no Kubernetes
volume). Plain psycopg, not corpus: the job runs every minute in 128 Mi.

    uptime_log.py "2026-11-08T07:00:00Z / 000 30.001"     add one line
    uptime_log.py --import FILE                          add the lines of an old uptime.log (once, when the volume goes)
    uptime_log.py --show [N]                             the last N lines (default 50)
"""
import os, sys
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

KEY = "uptime:failures"
KEEP_DAYS = 90


def add(conn, lines):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("""INSERT INTO kv (k, v) VALUES (%(k)s, '[]'::jsonb) ON CONFLICT (k) DO NOTHING""", {"k": KEY})
    conn.execute("""UPDATE kv SET v = (SELECT coalesce(jsonb_agg(x ORDER BY x #>> '{}'), '[]'::jsonb)
                                      FROM (SELECT DISTINCT x FROM jsonb_array_elements(kv.v || %(new)s) x) d
                                      WHERE (x #>> '{}') >= %(cutoff)s)
                    WHERE k = %(k)s""", {"k": KEY, "new": Jsonb(lines), "cutoff": cutoff})


def main(argv):
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        if argv[:1] == ["--import"]:
            with open(argv[1], encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            add(conn, lines)
            print(f"imported {len(lines)} lines")
        elif argv[:1] == ["--show"]:
            n = int(argv[1]) if len(argv) > 1 else 50
            row = conn.execute("SELECT v FROM kv WHERE k = %s", (KEY,)).fetchone()
            for ln in (row[0] if row else [])[-n:]:
                print(ln)
        else:
            add(conn, [" ".join(argv)])


if __name__ == "__main__":
    main(sys.argv[1:])
