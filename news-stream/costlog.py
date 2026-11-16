"""Usage records for the cost profile (Deyao, 2026-10-16: "make sure we are collecting usage data, so we can do a cost
analysis … see what each component uses … similar to running a profiler"). Records only: nothing here decides what or
how much a job does (YYC CLAUDE.md, "Budgets are safety stops").

  job()        the job a call belongs to: env YYC_JOB, else the Kubernetes workload from the pod's name (a CronJob's
               pod <cronjob>-<minutes>-<rand>, a Deployment's <deploy>-<rs hash>-<rand>), else the script's name.
               ai_runs.job (ai.py), paid_calls.job and the proxy bytes per job (resi.count) carry it.
  record()     one paid API request (TikHub, AnyAPI) in table paid_calls: provider, job, endpoint, HTTP status, ok,
               and the cost when the provider states it in the response. TikHub's prices are its own endpoint list
               (user/get_all_endpoints_info), applied by infra/cost-profile.py.
  snapshot()   the monitor's balances panel (every 5 min) stored in provider_snapshots, one row per provider: the
               providers' own counters (OpenRouter key usage, TikHub's day usage and per-endpoint counts, IPRoyal's
               sub-user traffic, AnyAPI and the solvers' balances), differenced over a window by infra/cost-profile.py.
Logging never breaks the work: a failed insert is printed to stderr and the call goes on.
"""
import os, re, sys

import corpus

_job = None
CRONJOB_POD = re.compile(r"^(.+)-\d{6,}-[a-z0-9]{5}$")
DEPLOYMENT_POD = re.compile(r"^(.+)-[a-z0-9]{6,10}-[a-z0-9]{5}$")


def job():
    global _job
    if _job:
        return _job
    j = os.environ.get("YYC_JOB")
    if not j and os.environ.get("KUBERNETES_SERVICE_HOST"):
        host = os.environ.get("HOSTNAME", "")
        m = CRONJOB_POD.match(host) or DEPLOYMENT_POD.match(host)
        j = m.group(1) if m else host or None
    if not j:
        j = os.path.splitext(os.path.basename(sys.argv[0] or "python"))[0] or "python"
    _job = j
    return j


def record(provider, endpoint, status=200, ok=True, usd=None, basis=None, units=1):
    try:
        corpus.db().execute("""INSERT INTO paid_calls (provider, job, endpoint, status, ok, units, usd, basis)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                            (provider, job(), endpoint, status, bool(ok), units, usd, basis))
    except Exception as e:  # noqa: BLE001
        print(f"costlog: recording {provider} {endpoint} failed: {e!r}"[:300], file=sys.stderr)


def snapshot(providers):
    """store the balances panel's providers (one row each) in provider_snapshots"""
    try:
        db = corpus.db()
        for p in providers or []:
            db.execute("INSERT INTO provider_snapshots (provider, status, data) VALUES (%s, %s, %s)",
                       (p.get("id") or "?", p.get("status"), corpus.J(p)))
    except Exception as e:  # noqa: BLE001
        print(f"costlog: snapshot failed: {e!r}"[:300], file=sys.stderr)
