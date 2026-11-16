#!/usr/bin/env python3
"""switchover.py CLUSTER TARGET: move a CloudNativePG cluster's primary to the standby TARGET (e.g. pg pg-3) with zero
downtime (Deyao, 2026-11-09).

A switchover stops the old primary: every connection to it is cut and new ones are refused until the new primary
serves, a few seconds. The apps run cut work again (news-stream corpus.retrying, aviary-pg's retry, ai-watch's
db.retrying, the web app's and yyc-mcp's db.ts, tools' CallLog; egress buffers its rows), which is safe only when the
cut work wrote nothing: a write transaction cut at its COMMIT may have committed. So this script fences writes first:
in every database it takes an EXCLUSIVE lock on every table (one session per database, `lock_timeout` 200 ms, tried
again until all are held, at most FENCE_FOR seconds, else everything is let go and it stops), which waits for the
write transactions already running to finish and holds new writes before their first write; reads go on (EXCLUSIVE
does not block ACCESS SHARE). Then the switchover: a CHECKPOINT first (the old primary's shutdown checkpoint is then
short), the operator told the target (what `kubectl cnpg promote` does), and the new primary timed until it serves.
The fence's sessions end with the old primary.

The cluster's primaryUpdateStrategy is supervised (pg.yaml, magpie-pg.yaml), so the operator never switches over on
its own; a change that restarts the copies restarts the standbys and waits for this script.
Env: KUBECONFIG. Run in Deyao's working hours (CLAUDE.md: planned risky changes).
"""
import datetime, json, subprocess, sys, time, uuid

FENCE_FOR = 15.0   # seconds to get every lock before giving up (writes wait meanwhile in the databases already fenced)
LAG_MAX = 16 << 20  # bytes a standby may trail before the switchover starts
SERVE_FOR = 120    # seconds for the new primary to serve


def k(*a, check=True):
    return subprocess.run(["kubectl", "-n", "db", *a], capture_output=True, text=True, check=check).stdout


def cluster(name):
    return json.loads(k("get", "cluster", name, "-o", "json"))


def sql(pod, db, q):
    return k("exec", pod, "-c", "postgres", "--", "psql", "-qAtX", "-d", db, "-c", q).strip()


def say(*a):
    print(datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S.%f")[:-3], *a, flush=True)


class Fence:
    """one psql session on the primary holding EXCLUSIVE locks on every table of one database"""
    def __init__(self, pod, db, tables):
        self.db, self.tables, self.held = db, tables, False
        self.p = subprocess.Popen(["kubectl", "-n", "db", "exec", "-i", pod, "-c", "postgres", "--", "psql", "-qAtX",
                                   "-d", db], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, bufsize=1)
        self.ask("SET lock_timeout = '200ms'; SET application_name = 'switchover-fence';")

    def ask(self, q):
        mark = uuid.uuid4().hex
        self.p.stdin.write(f"{q}\n\\echo {mark}\n")
        self.p.stdin.flush()
        out = []
        while (line := self.p.stdout.readline()):
            if line.strip() == mark:
                return "".join(out)
            out.append(line)
        raise RuntimeError(f"fence session for {self.db} ended: {''.join(out)[-300:]}")

    def try_lock(self):
        out = self.ask(f"BEGIN; LOCK TABLE {self.tables} IN EXCLUSIVE MODE; SELECT 'fence-held';")
        if "fence-held" in out:
            self.held = True
        else:
            self.ask("ROLLBACK;")
        return self.held

    def release(self):
        try:
            self.ask("ROLLBACK;")
        except Exception:  # noqa: BLE001: the session may be gone with the old primary
            pass

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        self.p.kill()


def healthy(c):
    """every copy ready; the phase healthy, or waiting for this script (a supervised update restarted the standbys)"""
    s = c.get("status", {})
    return s.get("readyInstances") == c["spec"]["instances"] and s.get("phase") in (
        "Cluster in healthy state", "Waiting for user action")


def main(name, target):
    c = cluster(name)
    primary = c["status"]["currentPrimary"]
    if target == primary:
        sys.exit(f"{target} is already {name}'s primary")
    if target not in c["status"].get("instanceNames", []):
        sys.exit(f"{target} is not an instance of {name}: {c['status'].get('instanceNames')}")
    if not healthy(c):
        sys.exit(f"{name} is not healthy: {c['status'].get('phase')}, {c['status'].get('readyInstances')} ready")
    rep = sql(primary, "postgres", "select application_name, sync_state, "
              "coalesce(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn), 0)::bigint from pg_stat_replication "
              "where state = 'streaming'")
    lags = {r.split("|")[0]: (r.split("|")[1], int(r.split("|")[2])) for r in rep.splitlines() if r}
    if target not in lags or lags[target][1] > LAG_MAX:
        sys.exit(f"{target} is not streaming within {LAG_MAX} bytes: {lags}")
    say(f"{name}: primary {primary} -> {target}; standbys {lags}")

    dbs = sql(primary, "postgres", "select datname from pg_database where datallowconn and not datistemplate "
              "and datname <> 'postgres' order by 1").split()
    tables = {}
    for db in dbs:
        t = sql(primary, db, "select string_agg(format('%I.%I', schemaname, tablename), ', ' order by 1) "
                "from pg_tables where schemaname not in ('pg_catalog', 'information_schema')")
        if t:
            tables[db] = t
    say(f"databases fenced: {', '.join(f'{d} ({v.count(chr(44)) + 1} tables)' for d, v in tables.items())}")
    sql(primary, "postgres", "CHECKPOINT")
    say("checkpoint done")

    fences = [Fence(primary, db, t) for db, t in tables.items()]
    t0 = time.monotonic()
    try:
        while not all(f.held for f in fences):
            for f in fences:
                if not f.held:
                    f.try_lock()
            if time.monotonic() - t0 > FENCE_FOR:
                blockers = sql(primary, "postgres", "select string_agg(format('%s %s %s %s', pid, datname, "
                               "application_name, left(query, 60)), E'\\n') from pg_stat_activity where "
                               "backend_xid is not null and application_name <> 'switchover-fence'")
                for f in fences:
                    f.release()
                sys.exit(f"could not fence writes within {FENCE_FOR} s (released; nothing moved). "
                         f"Open write transactions:\n{blockers}")
        fenced = time.monotonic()
        say(f"writes fenced in {fenced - t0:.2f} s; switching over")
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        k("patch", "cluster", name, "--subresource=status", "--type=merge", "-p", json.dumps({"status": {
            "targetPrimary": target, "targetPrimaryTimestamp": now, "phase": "Switchover in progress",
            "phaseReason": f"Switching over to {target} (db/switchover.py)"}}))
        while True:
            c = cluster(name)
            if c["status"].get("currentPrimary") == target:
                try:
                    if sql(target, "postgres", "select not pg_is_in_recovery()") == "t":
                        break
                except subprocess.CalledProcessError:
                    pass
            if time.monotonic() - fenced > SERVE_FOR:
                sys.exit(f"{target} did not serve as primary within {SERVE_FOR} s: {c['status'].get('phase')}")
            time.sleep(0.2)
        served = time.monotonic()
        say(f"{target} serves as primary: writes held {served - t0:.2f} s in all "
            f"({served - fenced:.2f} s of it the switchover)")
    finally:
        for f in fences:
            f.close()
    for _ in range(300):
        c = cluster(name)
        if healthy(c):
            say(f"{name} healthy: {c['status'].get('readyInstances')} ready, primary {c['status']['currentPrimary']}")
            return
        time.sleep(2)
    sys.exit(f"{name} not healthy 10 min after: {c['status'].get('phase')}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
