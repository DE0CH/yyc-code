#!/usr/bin/env python3
"""One-time move of the app's state from the old server's files into Postgres (DATABASE_URL).

    DATABASE_URL=postgres://… python3 migrate_to_postgres.py --data /var/lib/yyc/data [--owner EMAIL] [--check-only]

Reads (never writes) the old data directory: corpus.db (SQLite: posts, tags, kv), state.json + archive/<day>.json (the
stream), profiles.json (profiles, stream delivery settings, settings) and the jobs' documents (browser.json,
instagram.json, tikhub.json, facebook.json, fb-ids.json, fb-session.json, fb-comment-queries.json, comments.json,
social_runs.json). Writes:

  posts, tags                    as they are (same post ids: /post/<id> links and tags keep working)
  kv                             every key except the ones below
  ai_spend                       kv ai_spend:<day> (per purpose; "_total" is computed when read)
  ai_runs                        kv ai_runs:<purpose> (the last 30 calls per purpose)
  ai_conversations, ai_turns     kv agent:<id> (with its context, the AI summary it opened from, and that summary as
                                 the attached first turn); the old conversations had no owner: they go to --owner
                                 (default ADMIN_EMAIL, else chendeyao000@gmail.com). kv ctx:<id> (the posts a summary
                                 read) is copied with the rest of kv.
  stream_items                   archive/<day>.json (+ any state.json item missing from it), in the order seen
  poll_seen, poll_state          state.json seen, last_poll, source_status, discuss_at
  profiles, settings             profiles.json
  kv file:<name>                 the jobs' documents

Idempotent: rows already in Postgres are kept (ON CONFLICT DO NOTHING; the keyless ai_runs and ai_turns are only
written with their parent the first time). At the end it counts what the files hold and what Postgres holds, item by
item, and exits 1 when anything from the files is missing in Postgres. --check-only: only that comparison.
"""
import argparse, glob, json, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import corpus  # noqa: E402

DOCS = ["browser.json", "instagram.json", "tikhub.json", "facebook.json", "fb-ids.json", "fb-session.json",
        "fb-comment-queries.json", "comments.json", "social_runs.json"]
POST_COLS = ["id", "url", "sid", "source", "website", "board", "author", "platform", "source_type", "region", "title",
             "text", "hay", "published", "seen", "day", "engagement", "cached_at", "section", "thread"]
TAG_COLS = ["profile", "post_id", "relevant", "sentiment", "labels", "theme", "tagged_at"]
SPECIAL = ("ai_spend:", "ai_runs:", "agent:")


def clean(v):
    """Postgres text holds no NUL and no lone surrogate"""
    if isinstance(v, str):
        v = v.replace("\x00", "")
        try:
            v.encode("utf-8")
        except UnicodeEncodeError:
            v = v.encode("utf-8", "replace").decode("utf-8")
    return v


def item_day(it):
    """app.item_day: the HKT day of an item (published, else seen)"""
    from datetime import datetime
    return datetime.fromisoformat(it.get("published") or it["seen"]).astimezone(corpus.HKT).strftime("%Y-%m-%d")


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


class Source:
    """everything the old files hold"""
    def __init__(self, data):
        self.data = data
        self.sq = sqlite3.connect(f"file:{os.path.join(data, 'corpus.db')}?mode=ro", uri=True)
        self.state = load(os.path.join(data, "state.json"), {}) or {}
        self.profiles = load(os.path.join(data, "profiles.json"), {}) or {}
        self.kv = {k: json.loads(v) for k, v in self.sq.execute("SELECT k, v FROM kv")}

    def stream_items(self):
        """(day, stream, url, item) in the order the poller saw them, deduplicated by (day, stream, url)"""
        rows, keys = [], set()
        files = sorted(glob.glob(os.path.join(self.data, "archive", "*.json")))
        for f in files:
            for it in load(f, []):
                rows.append((os.path.basename(f)[:-5], it))
        for it in self.state.get("items", []):  # state.json's items are in the archive too; any that isn't
            rows.append((item_day(it), it))
        out = []
        for day, it in rows:
            k = (day, it["stream"], it["url"])
            if k not in keys:
                keys.add(k)
                out.append((day, it["stream"], it["url"], it))
        idx = {id(r): i for i, r in enumerate(out)}
        out.sort(key=lambda r: (r[3].get("seen") or "", idx[id(r)]))
        return out


def migrate(src, owner):
    c = corpus.db()
    n_posts = src.sq.execute("SELECT count(*) FROM posts").fetchone()[0]
    print(f"posts: {n_posts} to copy", flush=True)
    with corpus.pool().connection() as conn:
        with conn.transaction():
            conn.execute("CREATE TEMP TABLE posts_in (LIKE posts INCLUDING DEFAULTS) ON COMMIT DROP")
            with conn.cursor().copy(f"COPY posts_in ({','.join(POST_COLS)}) FROM STDIN") as cp:
                for r in src.sq.execute(f"SELECT {','.join(POST_COLS)} FROM posts ORDER BY id"):
                    cp.write_row([clean(v) for v in r])
            n = conn.execute(f"""INSERT INTO posts ({','.join(POST_COLS)}) OVERRIDING SYSTEM VALUE
                                 SELECT {','.join(POST_COLS)} FROM posts_in ORDER BY id ON CONFLICT DO NOTHING""").rowcount
            print(f"posts: {n} inserted", flush=True)
            conn.execute("SELECT setval(pg_get_serial_sequence('posts', 'id'), greatest((SELECT max(id) FROM posts), 1))")
        with conn.transaction():
            conn.execute("CREATE TEMP TABLE tags_in (LIKE tags) ON COMMIT DROP")
            with conn.cursor().copy(f"COPY tags_in ({','.join(TAG_COLS)}) FROM STDIN") as cp:
                for r in src.sq.execute(f"SELECT {','.join(TAG_COLS)} FROM tags"):
                    cp.write_row([clean(v) for v in r])
            n = conn.execute(f"""INSERT INTO tags ({','.join(TAG_COLS)}) SELECT {','.join(TAG_COLS)} FROM tags_in t
                                 WHERE EXISTS (SELECT 1 FROM posts p WHERE p.id=t.post_id) ON CONFLICT DO NOTHING""").rowcount
            print(f"tags: {n} inserted", flush=True)

    with c:
        rows = [(k, corpus.J(v)) for k, v in src.kv.items() if not k.startswith(SPECIAL)]
        c.executemany("INSERT INTO kv (k, v) VALUES (%s, %s) ON CONFLICT (k) DO NOTHING", rows)
        for k, v in src.kv.items():
            if k.startswith("ai_spend:"):
                c.executemany("INSERT INTO ai_spend (day, purpose, usd) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                              [(k[len("ai_spend:"):], p, float(u)) for p, u in v.items() if p != "_total"])
            elif k.startswith("ai_runs:"):
                purpose = k[len("ai_runs:"):]
                if corpus.kv_get("migrated:" + k) is None:
                    c.executemany("""INSERT INTO ai_runs (purpose, model, usd, input_tokens, output_tokens, chars, calls)
                                     VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                                  [(purpose, r.get("model"), r.get("usd"), r.get("input_tokens"), r.get("output_tokens"),
                                    r.get("chars"), r.get("calls")) for r in v])
                    corpus.kv_set("migrated:" + k, len(v))
            elif k.startswith("agent:"):
                sid = k[len("agent:"):]
                turns = v.get("turns") or []
                created = v.get("created") or (turns[0]["at"] if turns else None)
                updated = turns[-1]["at"] if turns else created
                new = c.execute("""INSERT INTO ai_conversations (id, email, title, created, updated, context)
                                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING RETURNING id""",
                                (sid, owner, v.get("title"), created, updated, v.get("context"))).fetchone()
                if new:
                    c.executemany("""INSERT INTO ai_turns (conversation, q, a, at, tables, followups, steps, usage, attached)
                                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                  [(sid, t.get("q") or "", t.get("a"), t.get("at"),
                                    None if t.get("attached") else corpus.J(t.get("tables") or []),
                                    None if t.get("attached") else corpus.J(t.get("followups") or []),
                                    None if t.get("attached") else corpus.J(t.get("steps") or []),
                                    None if t.get("attached") else corpus.J(t.get("usage") or {}),
                                    bool(t.get("attached"))) for t in turns])

        items = src.stream_items()
        c.executemany("""INSERT INTO stream_items (day, stream, url, item) VALUES (%s, %s, %s, %s)
                         ON CONFLICT (day, stream, url) DO NOTHING""",
                      [(d, s, u, corpus.J(it)) for d, s, u, it in items])
        st = src.state
        at = st.get("last_poll")
        c.executemany("INSERT INTO poll_seen (url, at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                      [(u, at) for u in dict.fromkeys(st.get("seen", []))])
        for k in ("last_poll", "source_status", "discuss_at"):
            if k in st:
                c.execute("INSERT INTO poll_state (k, v) VALUES (%s, %s) ON CONFLICT DO NOTHING", (k, corpus.J(st[k])))

        pr = src.profiles
        for p in pr.get("profiles", []):
            c.execute("INSERT INTO profiles (id, kind, data) VALUES (%s, 'custom', %s) ON CONFLICT DO NOTHING",
                      (p["id"], corpus.J(p)))
        for sid, d in pr.get("stream_settings", {}).items():
            c.execute("INSERT INTO profiles (id, kind, data) VALUES (%s, 'stream', %s) ON CONFLICT DO NOTHING",
                      (sid, corpus.J(d)))
        c.executemany("INSERT INTO settings (k, v) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                      [(k, corpus.J(v)) for k, v in pr.get("settings", {}).items()])

        for name in DOCS:
            d = load(os.path.join(src.data, name))
            if d is not None:
                c.execute("INSERT INTO kv (k, v) VALUES (%s, %s) ON CONFLICT DO NOTHING", ("file:" + name, corpus.J(d)))


def check(src, owner):
    """files vs Postgres, item by item → [(what, in files, in Postgres, missing)]"""
    db = corpus.db()
    out = []

    def row(what, want, have):
        out.append((what, len(want), len(have), len(set(want) - set(have))))

    sq = src.sq
    want = {r[0]: r[1:] for r in sq.execute("SELECT id, url, day, coalesce(length(hay),0) FROM posts")}
    have = {r[0]: tuple(r[1:]) for r in db.execute("SELECT id, url, day, coalesce(length(hay),0) FROM posts")}
    row("posts (id, url, day, search text length)", [(k,) + v for k, v in want.items()],
        [(k,) + v for k, v in have.items()])
    row("tags (profile, post, relevant, sentiment, labels, theme)",
        [tuple(r) for r in sq.execute("SELECT profile, post_id, relevant, sentiment, labels, theme FROM tags")],
        [tuple(r) for r in db.execute("SELECT profile, post_id, relevant, sentiment, labels, theme FROM tags")])
    row("kv (key, value)", [(k, json.dumps(v, sort_keys=True)) for k, v in src.kv.items() if not k.startswith(SPECIAL)],
        [(r[0], json.dumps(r[1], sort_keys=True)) for r in db.execute("SELECT k, v FROM kv")])
    row("ai_spend (day, purpose, usd)",
        [(k[9:], p, round(u, 6)) for k, v in src.kv.items() if k.startswith("ai_spend:") for p, u in v.items() if p != "_total"],
        [(r[0], r[1], round(r[2], 6)) for r in db.execute("SELECT day, purpose, usd FROM ai_spend")])
    row("ai_runs (purpose, n-th, usd)",
        [(k[8:], i, r.get("usd")) for k, v in src.kv.items() if k.startswith("ai_runs:") for i, r in enumerate(v)],
        [(r[0], r[1] - 1, r[2]) for r in db.execute(
            "SELECT purpose, row_number() OVER (PARTITION BY purpose ORDER BY id), usd FROM ai_runs")])
    row("ai_conversations (id, owner, context)", [(k[6:], owner, v.get("context")) for k, v in src.kv.items() if k.startswith("agent:")],
        [tuple(r) for r in db.execute("SELECT id, email, context FROM ai_conversations")])
    row("ai_turns (conversation, question, answer, attached)",
        [(k[6:], t.get("q") or "", t.get("a"), bool(t.get("attached"))) for k, v in src.kv.items() if k.startswith("agent:")
         for t in v.get("turns", [])],
        [tuple(r) for r in db.execute("SELECT conversation, q, a, attached FROM ai_turns")])
    row("kv ctx:* (the posts each AI summary read)", [(k,) for k in src.kv if k.startswith("ctx:")],
        [tuple(r) for r in db.execute("SELECT k FROM kv WHERE k LIKE 'ctx:%%'")])
    row("stream_items (day, stream, url)", [r[:3] for r in src.stream_items()],
        [tuple(r) for r in db.execute("SELECT day, stream, url FROM stream_items")])
    row("poll_seen (url)", [(u,) for u in set(src.state.get("seen", []))], [tuple(r) for r in db.execute("SELECT url FROM poll_seen")])
    row("poll_state (key)", [(k,) for k in ("last_poll", "source_status", "discuss_at") if k in src.state],
        [tuple(r) for r in db.execute("SELECT k FROM poll_state")])
    pr = src.profiles
    row("profiles (id, kind)", [(p["id"], "custom") for p in pr.get("profiles", [])] +
        [(k, "stream") for k in pr.get("stream_settings", {})], [tuple(r) for r in db.execute("SELECT id, kind FROM profiles")])
    row("settings (key)", [(k,) for k in pr.get("settings", {})], [tuple(r) for r in db.execute("SELECT k FROM settings")])
    row("job documents (kv file:<name>)", [("file:" + n,) for n in DOCS if os.path.exists(os.path.join(src.data, n))],
        [tuple(r) for r in db.execute("SELECT k FROM kv WHERE k LIKE 'file:%%'")])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="the old DATA_DIR (read only)")
    ap.add_argument("--owner", default=os.environ.get("ADMIN_EMAIL", "chendeyao000@gmail.com"),
                    help="the Access account the old agent conversations go to")
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    src = Source(a.data)
    if not a.check_only:
        migrate(src, a.owner.lower())
    rows = check(src, a.owner.lower())
    w = max(len(r[0]) for r in rows)
    print(f"\n{'':{w}}  {'files':>8}  {'postgres':>8}  missing")
    for what, n_src, n_db, missing in rows:
        print(f"{what:{w}}  {n_src:>8}  {n_db:>8}  {missing}")
    left = [r[0] for r in rows if r[3]]
    print("\nOK: everything in the files is in Postgres" if not left else f"\nMISSING in Postgres: {left}")
    others = sorted(set(os.listdir(a.data)) - set(DOCS) - {"corpus.db", "corpus.db-wal", "corpus.db-shm", "state.json",
                                                           "profiles.json", "archive", "reports"})
    print("not migrated (files that stay on the volume or that no code reads):", others)
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
