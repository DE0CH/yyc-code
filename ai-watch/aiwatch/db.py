"""The app's database: database `aiwatch` (owner aiwatch) on YYC's own Postgres (Service pg-rw.db), DATABASE_URL.

A case (個案) is one subject (an event, a person or a company). Its work runs as tasks in one queue (table task), taken
with FOR UPDATE SKIP LOCKED, so any number of workers share it and a worker that dies leaves its task to be taken again
once its lease runs out. Browser logins are one storage state (table browser_state), read by every worker at start and
written back after its runs.
"""
import json
import os
import socket
import threading
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS case_ (
  id serial PRIMARY KEY,
  subject text NOT NULL,
  kind text NOT NULL,                    -- event | person | company
  context text NOT NULL DEFAULT '',      -- what the user added (who they are, what to focus on)
  engines text[] NOT NULL,
  status text NOT NULL DEFAULT 'new',    -- new | news | questions | ready | asking | analysing | done | failed
  round int NOT NULL DEFAULT 1,
  created_by text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  analysed_at timestamptz,
  summary jsonb                          -- the overview numbers the case page shows
);
CREATE TABLE IF NOT EXISTS issue (       -- 近期議題: what the news says about the subject now
  id serial PRIMARY KEY,
  case_id int NOT NULL REFERENCES case_ ON DELETE CASCADE,
  title text NOT NULL,
  summary text NOT NULL DEFAULT '',
  latest date,
  links jsonb NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS news (        -- the news found for the subject (the case's timeline)
  id serial PRIMARY KEY,
  case_id int NOT NULL REFERENCES case_ ON DELETE CASCADE,
  url text NOT NULL,
  title text NOT NULL,
  outlet text NOT NULL DEFAULT '',
  published date,
  UNIQUE (case_id, url)
);
CREATE TABLE IF NOT EXISTS question (
  id serial PRIMARY KEY,
  case_id int NOT NULL REFERENCES case_ ON DELETE CASCADE,
  text text NOT NULL,
  stakeholder text NOT NULL DEFAULT '',  -- 記者, 一般市民, 投資者 ...
  angle text NOT NULL DEFAULT 'neutral', -- positive | negative | neutral | base
  issue_id int REFERENCES issue ON DELETE SET NULL,
  selected boolean NOT NULL DEFAULT false,
  round int NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ask (         -- one question put to one app, and what it answered
  id serial PRIMARY KEY,
  case_id int NOT NULL REFERENCES case_ ON DELETE CASCADE,
  question_id int NOT NULL REFERENCES question ON DELETE CASCADE,
  engine text NOT NULL,
  status text NOT NULL DEFAULT 'queued', -- queued | done | none (the app showed no answer) | failed | login_needed
  answer text,
  citations jsonb NOT NULL DEFAULT '[]',
  meta jsonb NOT NULL DEFAULT '{}',
  error text,
  asked_at timestamptz,
  tone int,                              -- the answer's tone towards the subject, -1/0/1 (analysis)
  mentions boolean,                      -- the answer names the subject (analysis)
  latest_date date,                      -- the newest date the answer writes (analysis)
  UNIQUE (question_id, engine)
);
CREATE TABLE IF NOT EXISTS claim (       -- one statement an answer makes (analysis)
  id serial PRIMARY KEY,
  case_id int NOT NULL REFERENCES case_ ON DELETE CASCADE,
  ask_id int NOT NULL REFERENCES ask ON DELETE CASCADE,
  text text NOT NULL,
  tone int NOT NULL DEFAULT 0,
  cited jsonb NOT NULL DEFAULT '[]',     -- the citation URLs the answer puts on it
  support text,                          -- what the cited pages say of it: supported | partial | absent (sources)
  group_id int
);
CREATE TABLE IF NOT EXISTS claim_group ( -- the same statement across answers and apps: agreement, contradiction, risk
  id serial PRIMARY KEY,
  case_id int NOT NULL REFERENCES case_ ON DELETE CASCADE,
  text text NOT NULL,
  tone int NOT NULL DEFAULT 0,
  engines text[] NOT NULL DEFAULT '{}',
  contradiction text,                    -- how the apps disagree, if they do
  support text,                          -- supported | partial | unsupported | unread (the cited pages checked)
  support_note text,
  impact int,                            -- 0-100: how much it can hurt or help the subject's image
  actionable int,                        -- 0-100: how much PR can do about it
  risk int,                              -- the ranking score
  issue_id int REFERENCES issue ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS source (      -- a page the apps cited, read and summarised
  id serial PRIMARY KEY,
  case_id int NOT NULL REFERENCES case_ ON DELETE CASCADE,
  url text NOT NULL,
  domain text NOT NULL,
  kind text,                             -- official | media | wiki | forum | social | business | other
  title text,
  status text NOT NULL DEFAULT 'queued', -- queued | read | unreadable
  summary text,
  tone int,
  text_chars int,
  engines text[] NOT NULL DEFAULT '{}',
  cited_by int NOT NULL DEFAULT 0,
  UNIQUE (case_id, url)
);
ALTER TABLE claim_group ADD COLUMN IF NOT EXISTS category text;  -- 事實 | 責任 | 聲譽 | 回應 | 預測 | 其他
CREATE TABLE IF NOT EXISTS entity (      -- people, organisations, places, laws the statements name (the graph)
  id serial PRIMARY KEY,
  case_id int NOT NULL REFERENCES case_ ON DELETE CASCADE,
  name text NOT NULL,
  kind text NOT NULL DEFAULT '其他',       -- 人物 | 機構 | 公司 | 地點 | 法例 | 事件 | 其他
  UNIQUE (case_id, name)
);
CREATE TABLE IF NOT EXISTS group_entity (
  group_id int NOT NULL REFERENCES claim_group ON DELETE CASCADE,
  entity_id int NOT NULL REFERENCES entity ON DELETE CASCADE,
  PRIMARY KEY (group_id, entity_id)
);
CREATE TABLE IF NOT EXISTS forum_hit (   -- LIHKG / Threads posts about the subject (Google, site: search)
  id serial PRIMARY KEY,
  case_id int NOT NULL REFERENCES case_ ON DELETE CASCADE,
  span text NOT NULL,                    -- week | month | year | any
  site text NOT NULL,
  url text NOT NULL,
  title text NOT NULL,
  snippet text NOT NULL DEFAULT '',
  posted date,
  found_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (case_id, span, url)
);
CREATE TABLE IF NOT EXISTS suggestion (  -- AI 建議下一輪問題
  id serial PRIMARY KEY,
  case_id int NOT NULL REFERENCES case_ ON DELETE CASCADE,
  text text NOT NULL,
  why text NOT NULL DEFAULT '',
  made_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS task (
  id bigserial PRIMARY KEY,
  case_id int REFERENCES case_ ON DELETE CASCADE,
  kind text NOT NULL,                    -- news | questions | ask | extract | sources | group | summary
  ref int,                               -- the ask / question / source id the task is about
  engine text,                           -- ask tasks: the app (a worker runs one at a time per app)
  status text NOT NULL DEFAULT 'queued', -- queued | running | done | failed
  tries int NOT NULL DEFAULT 0,
  run_after timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz,
  worker text,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  done_at timestamptz
);
ALTER TABLE task ADD COLUMN IF NOT EXISTS arg text;  -- a step's parameter (forums: the time span)
CREATE INDEX IF NOT EXISTS task_queue ON task (status, run_after) WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS browser_state (
  id int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  state jsonb NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  note text
);
ALTER TABLE browser_state ADD COLUMN IF NOT EXISTS login_at timestamptz;  -- the last save by login.py
CREATE TABLE IF NOT EXISTS engine_status (  -- what the workers last saw of each app (login_needed, blocked, ok)
  engine text PRIMARY KEY,
  status text NOT NULL,
  detail text,
  at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS llm_call (    -- every model call with OpenRouter's own cost (usage.cost)
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  case_id int,
  step text NOT NULL,
  model text NOT NULL,
  prompt_tokens int,
  completion_tokens int,
  cost_usd numeric(12, 6)
);
"""

_pool, _lock = None, threading.Lock()
WORKER = os.environ.get("HOSTNAME") or socket.gethostname()


# A planned primary move (a CloudNativePG switchover) cuts every connection and refuses new ones for a few seconds:
# statements and transactions caught by it run again on the new primary (retrying), so a page or a worker waits instead
# of failing (Deyao, 2026-11-09: zero downtime). Safe because the switchover fences writes first (YYC
# news-stream/deploy/clusters/talos/db/switchover.sh locks every table EXCLUSIVE before the old primary stops).
RETRY_FOR, RETRY_GAP = 30.0, 0.2
from psycopg_pool.base import AttemptWithBackoff  # noqa: E402
AttemptWithBackoff.INITIAL_DELAY, AttemptWithBackoff.DELAY_BACKOFF = RETRY_GAP, 1.0  # reopen every 0.2 s, no backoff


def transient(e):
    """the database away for a moment (class 08, 57P01-03, 25006, a lost connection, the pool's wait)"""
    from psycopg_pool import PoolTimeout
    if isinstance(e, PoolTimeout):
        return True
    if not isinstance(e, psycopg.Error):
        return False
    st = getattr(e, "sqlstate", None)
    if st:
        return st in {"57P01", "57P02", "57P03", "25006"} or st.startswith("08")
    return isinstance(e, psycopg.OperationalError)


def retrying(work):
    """work() → its value, run again every RETRY_GAP s for up to RETRY_FOR s while the database is away"""
    import time
    end = None
    while True:
        try:
            return work()
        except Exception as e:  # noqa: BLE001: only transient errors are kept
            if not transient(e):
                raise
            end = end or time.monotonic() + RETRY_FOR
            if time.monotonic() >= end:
                raise
            time.sleep(RETRY_GAP)


def pool():
    global _pool
    with _lock:
        if _pool is None:
            _pool = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=int(os.environ.get("DB_POOL", "6")),
                                   kwargs={"row_factory": dict_row, "autocommit": True,
                                           "keepalives": 1, "keepalives_idle": 30}, open=True)
        return _pool


@contextmanager
def conn():
    with pool().connection() as c:
        yield c


def transaction(fn):
    """fn(connection) in one transaction → its value, run again from the start when the database went away under it:
    fn must only touch the database"""
    def once():
        with conn() as c, c.transaction():
            return fn(c)
    return retrying(once)


def q(sql, *args):
    def once():
        with conn() as c:
            return c.execute(sql, args).fetchall()
    return retrying(once)


def one(sql, *args):
    def once():
        with conn() as c:
            return c.execute(sql, args).fetchone()
    return retrying(once)


def run(sql, *args):
    def once():
        with conn() as c:
            c.execute(sql, args)
    retrying(once)


def migrate():
    with conn() as c:
        c.execute("SELECT pg_advisory_lock(424242)")
        try:
            c.execute(SCHEMA)
        finally:
            c.execute("SELECT pg_advisory_unlock(424242)")


def J(x):
    return psycopg.types.json.Jsonb(x)


# ---- tasks

def add_task(kind, case_id=None, ref=None, engine=None, delay=0, arg=None):
    run("INSERT INTO task (case_id, kind, ref, engine, run_after, arg) VALUES (%s, %s, %s, %s, now() + make_interval(secs => %s), %s)",
        case_id, kind, ref, engine, delay, arg)


# apps that answer one question at a time per account: a second question waits in the app's own queue (Dola showed it
# above the input box and never answered while another worker's question ran, 2026-10-27), so they are asked by one
# worker at a time across all workers
ONE_AT_A_TIME = ["dola", "deepseek"]


def take_task(kinds, busy_engines=(), lease=900):
    """the next due task of these kinds (an ask task only for an app this worker isn't already asking, and for an app
    in ONE_AT_A_TIME only when no worker is asking it) → row or None. A running task whose lease ran out (its worker
    died) is due again."""
    def take(c):
        r = c.execute("""
          SELECT * FROM task t WHERE kind = ANY(%s)
            AND ((status = 'queued' AND run_after <= now()) OR (status = 'running' AND lease_until < now()))
            AND (engine IS NULL OR NOT engine = ANY(%s))
            AND (engine IS NULL OR NOT engine = ANY(%s) OR NOT EXISTS (
                  SELECT 1 FROM task o WHERE o.engine = t.engine AND o.status = 'running' AND o.lease_until >= now()))
          ORDER BY run_after, id LIMIT 1 FOR UPDATE SKIP LOCKED""",
                      (list(kinds), list(busy_engines), ONE_AT_A_TIME)).fetchone()
        if not r:
            return None
        c.execute("UPDATE task SET status = 'running', tries = tries + 1, worker = %s, lease_until = now() + make_interval(secs => %s) WHERE id = %s",
                  (WORKER, lease, r["id"]))
        return r
    return transaction(take)


def finish_task(tid, error=None, retry_in=None):
    if error and retry_in is not None:
        run("UPDATE task SET status = 'queued', error = %s, run_after = now() + make_interval(secs => %s), lease_until = NULL WHERE id = %s",
            error[:2000], retry_in, tid)
    else:
        run("UPDATE task SET status = %s, error = %s, done_at = now(), lease_until = NULL WHERE id = %s",
            "failed" if error else "done", (error or None) and error[:2000], tid)


def pending(case_id, kinds):
    return one("SELECT count(*) AS n FROM task WHERE case_id = %s AND kind = ANY(%s) AND status IN ('queued', 'running')",
               case_id, list(kinds))["n"]


# ---- browser state

def browser_state(with_time=False):
    """the stored state (and, with_time, when login.py last saved it)"""
    r = one("SELECT state, login_at FROM browser_state WHERE id = 1")
    if with_time:
        return (r["state"], r["login_at"]) if r else (None, None)
    return r["state"] if r else None


def save_browser_state(state, note=None, merge=False):
    """login.py replaces the state (and stamps login_at); a worker (merge=True) folds its cookies and storage into it,
    so a worker that never had a login (it started before login.py saved one) can't wipe it"""
    def save(c, state=state):
        if merge:
            r = c.execute("SELECT state FROM browser_state WHERE id = 1 FOR UPDATE").fetchone()
            if r:
                from .login import merge_states
                state = merge_states(r["state"], state, drop_google=False)
            c.execute("""INSERT INTO browser_state (id, state, note) VALUES (1, %s, %s)
                         ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state, updated_at = now(), note = EXCLUDED.note""",
                      (J(state), note))
        else:
            c.execute("""INSERT INTO browser_state (id, state, note, login_at) VALUES (1, %s, %s, now())
                         ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state, updated_at = now(), note = EXCLUDED.note,
                         login_at = now()""", (J(state), note))
    transaction(save)


def login_saved_at():
    """when login.py last saved the state (workers reload it when newer than what they started with)"""
    r = one("SELECT login_at FROM browser_state WHERE id = 1")
    return r["login_at"] if r else None


def engine_status(engine, status, detail=None):
    run("""INSERT INTO engine_status (engine, status, detail) VALUES (%s, %s, %s)
           ON CONFLICT (engine) DO UPDATE SET status = EXCLUDED.status, detail = EXCLUDED.detail, at = now()""",
        engine, status, detail and detail[:1000])


def dumps(x):
    return json.dumps(x, ensure_ascii=False, default=str)
