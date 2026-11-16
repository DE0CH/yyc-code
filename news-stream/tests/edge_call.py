"""Call one of the edge app's server data functions (web-app/src/server/<module>.ts) in Node against this test's
database (DATABASE_URL), through web-app/scripts/call.ts: the Python tests check what the site serves with the app's
own implementation. Needs web-app/node_modules and Postgres over TCP as claude:dev (the yyc-news-stream skill)."""
import json, os, subprocess

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web-app")


def _url():
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgresql:///"):
        url = "postgres://claude:dev@127.0.0.1:5432/" + url.rsplit("/", 1)[1]
    return url


def edge(fn, *args):
    """edge("item.aiScores", pid) → the function's result, as JSON. Publish the site's config first
    (edge_config.publish()) when the function reads it."""
    r = subprocess.run(["npx", "tsx", "scripts/call.ts", fn, json.dumps(list(args))], cwd=APP, capture_output=True,
                       text=True, env=dict(os.environ, DATABASE_URL=_url()), timeout=300)
    if r.returncode:
        raise RuntimeError(r.stderr[-2000:])
    return json.loads(r.stdout)
