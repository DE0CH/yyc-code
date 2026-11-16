#!/usr/bin/env python3
"""The manual trigger for a backlog (Deyao, 2026-10-27: "by default, backlog should not be cleared at all (at any stage
of the pipeline), only a manual trigger should process them"). Each stage works only on posts seen from its own start
point on and never on backlog posts (first_run); the start point moves to now by itself when the stage starts again
after a stop (corpus.STAGES, corpus.stage). This script shows the stages and moves a start point back.

    python3 infra/backlog.py                          every stage: its start point, why it was set, when it last
                                                      finished a round, whether it is blocked, and the posts it skips
                                                      (seen since corpus.FLOOR before its start point; backlog posts)
    python3 infra/backlog.py run STAGE --from TIME    the stage works on what was seen from TIME (ISO, e.g.
                                                      2026-10-27T10:00+08:00) on, from its next round; it goes on
                                                      from there until its next stop
    python3 infra/backlog.py run STAGE --from TIME --first-run
                                                      the same, the backlog posts (first_run: a source's first read, or
                                                      its first read after a gap) seen from TIME on too
    python3 infra/backlog.py run all …                every stage

The score backfill (posts older than streams.MAX_AGE when they came in) is its own switch: infra/thresholds.py backfill
on|off. Runs wherever DATABASE_URL reaches the database: from a session, news-stream/deploy/cluster/cluster.sh
db-forward and DATABASE_URL=postgresql://app:$YYC_PG_APP_PASSWORD@127.0.0.1:15432/app?sslmode=require
"""
import os, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import corpus  # noqa: E402


def status():
    c = corpus.db()
    for name in corpus.STAGES:
        v = corpus.kv_get("cutoff:" + name) or {}
        st = corpus.kv_get("stage:" + name) or {}
        cut, fr = corpus.cutoff(name), (v.get("first_run_from"))
        why = ((v.get("log") or [{}])[-1]).get("why", "the first start point (corpus.FLOOR)")
        before = c.execute("SELECT count(*) FROM posts WHERE thread IS NULL AND seen::timestamptz >= %s::timestamptz "
                           "AND seen::timestamptz < %s::timestamptz", (corpus.FLOOR, cut)).fetchone()[0]
        first = c.execute("SELECT count(*) FROM posts WHERE thread IS NULL AND first_run AND seen::timestamptz >= %s::timestamptz"
                          + (" AND seen::timestamptz < %s::timestamptz" if fr else ""),
                          (corpus.FLOOR,) + ((fr,) if fr else ())).fetchone()[0]
        print(f"{name:14} from {cut}  ({why})")
        print(f"{'':14} last round {st.get('ok') or '-'}" + (f"  BLOCKED since {st.get('down_since')}: {st['down']}" if st.get("down") else "")
              + f"  skipped: {before} posts seen before its start point, {first} backlog posts"
              + (f" (backlog posts from {fr} are in)" if fr else ""))


def main(a):
    if not a:
        return status()
    if a[0] == "run" and len(a) >= 4 and a[2] == "--from":
        frm = datetime.fromisoformat(a[3]).isoformat(timespec="seconds")
        names = list(corpus.STAGES) if a[1] == "all" else [a[1]]
        for n in names:
            if n not in corpus.STAGES:
                sys.exit(f"unknown stage {n}; stages: {', '.join(corpus.STAGES)}")
            got = corpus.backlog_run(n, frm, first_run="--first-run" in a)
            print(f"{n}: works from {got} on" + (" (backlog posts too)" if "--first-run" in a else ""))
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
