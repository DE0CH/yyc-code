"""The site's JSON API, https://aiwatch.yyc-internal.com/api/ (the pages, static/, are served from Cloudflare's edge by
Worker yyc-aiwatch, ai-watch/edge, which forwards /api/* here). Behind Cloudflare Access: the
tunnel `aiwatch` admits only requests with a valid token of the Access app (cloudflared checks it, tunnels.tf), and the
signed-in address arrives as Cf-Access-Authenticated-User-Email. Stateless: two replicas, rolling updates.

  GET  /api/cases                         the cases, newest first
  POST /api/cases                         {subject, kind, context, engines} → a new case (its news step starts)
  GET  /api/cases/<id>                    everything the case page shows
  POST /api/cases/<id>/ask                {question_ids} → tick these, ask them with the case's apps
  POST /api/cases/<id>/followup           {questions: [text]} → 追問: new questions, asked and analysed with the rest
  POST /api/cases/<id>/questions          a fresh set of suggested questions
  POST /api/cases/<id>/retry              the failed asks of the case asked again
  POST /api/cases/<id>/delete             the case and all it holds
  POST /api/cases/<id>/forums             {span: week|month|year|any} → LIHKG / Threads posts about the subject
  POST /api/cases/<id>/suggest            AI 建議下一輪問題 (the worker writes them; the page shows them)
  POST /api/cases/<id>/reanalyse          重新分析: the statements grouped again
  POST /api/cases/<id>/again              用這題重新提問: a new case on the same subject
  GET  /api/status                        each app's last state (login needed, blocked), the model key, this month's spend
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, pipeline



def case_full(cid):
    c = db.one("SELECT * FROM case_ WHERE id = %s", cid)
    if not c:
        return None
    out = {"case": c}
    out["issues"] = db.q("SELECT * FROM issue WHERE case_id = %s ORDER BY latest DESC NULLS LAST, id", cid)
    out["news"] = db.q("SELECT title, outlet, url, published FROM news WHERE case_id = %s ORDER BY published DESC NULLS LAST, id LIMIT 80", cid)
    out["questions"] = db.q("""SELECT q.*, (SELECT json_agg(json_build_object('engine', a.engine, 'status', a.status, 'tone', a.tone))
                                 FROM ask a WHERE a.question_id = q.id) AS asks
                               FROM question q WHERE q.case_id = %s ORDER BY q.round, q.id""", cid)
    out["asks"] = db.q("""SELECT a.id, a.question_id, a.engine, a.status, a.answer, a.citations, a.error, a.asked_at, a.tone,
                                 a.mentions, a.latest_date FROM ask a WHERE a.case_id = %s ORDER BY a.question_id, a.engine""", cid)
    out["groups"] = db.q("""SELECT g.*, (SELECT json_agg(json_build_object('ask_id', c.ask_id, 'text', c.text, 'support', c.support))
                              FROM claim c WHERE c.group_id = g.id) AS claims
                            FROM claim_group g WHERE g.case_id = %s ORDER BY g.risk DESC NULLS LAST, g.id""", cid)
    out["sources"] = db.q("""SELECT id, url, domain, kind, title, status, summary, tone, engines, cited_by FROM source
                             WHERE case_id = %s ORDER BY cited_by DESC, id""", cid)
    out["tasks"] = db.q("""SELECT kind, status, count(*) AS n FROM task WHERE case_id = %s GROUP BY kind, status""", cid)
    # the graph: entities with how often and in what tone the statements name them, and which statements name them
    out["entities"] = db.q("""SELECT e.id, e.name, e.kind, count(*) AS n, sum((g.tone < 0)::int) AS neg, sum((g.tone > 0)::int) AS pos,
                                     array_agg(g.id) AS groups, array_remove(array_agg(DISTINCT g.issue_id), NULL) AS issues
                              FROM entity e JOIN group_entity ge ON ge.entity_id = e.id JOIN claim_group g ON g.id = ge.group_id
                              WHERE e.case_id = %s GROUP BY e.id ORDER BY count(*) DESC""", cid)
    # each statement's cited pages (the trace view and the sources table)
    out["group_urls"] = db.q("""SELECT c.group_id, array_agg(DISTINCT u) AS urls FROM claim c, jsonb_array_elements_text(c.cited) u
                                WHERE c.case_id = %s AND c.group_id IS NOT NULL GROUP BY c.group_id""", cid)
    out["forums"] = db.q("SELECT span, site, url, title, snippet, posted, found_at FROM forum_hit WHERE case_id = %s ORDER BY posted DESC NULLS LAST, id", cid)
    out["suggestions"] = db.q("SELECT text, why, made_at FROM suggestion WHERE case_id = %s ORDER BY id", cid)
    out["cost"] = db.one("SELECT COALESCE(sum(cost_usd), 0) AS usd FROM llm_call WHERE case_id = %s", cid)["usd"]
    out["engines"] = pipeline.ENGINES
    return out


def signed_in():
    """the apps that need an account and have its session in the stored browser state (aiwatch/login.py's checks)"""
    st = db.browser_state() or {}
    names = {(c["domain"].lstrip("."), c["name"]) for c in st.get("cookies", [])}
    return {"perplexity": any(d.endswith("perplexity.ai") and "session-token" in n for d, n in names),
            "dola": any(d.endswith("dola.com") and n == "sessionid" for d, n in names)}


def status():
    return {"engines": db.q("SELECT * FROM engine_status ORDER BY engine"),
            "signed_in": signed_in(),
            "labels": pipeline.ENGINES,
            "month_usd": db.one("SELECT COALESCE(sum(cost_usd), 0) AS usd FROM llm_call WHERE at >= date_trunc('month', now() AT TIME ZONE 'Asia/Hong_Kong')")["usd"],
            "login_saved": db.one("SELECT updated_at, note FROM browser_state WHERE id = 1"),
            "queue": db.q("SELECT kind, count(*) AS n FROM task WHERE status IN ('queued', 'running') GROUP BY kind")}



MAX_BODY = 200_000  # bytes of a POST body (a subject, a context, a question)


class H(BaseHTTPRequestHandler):
    server_version = "aiwatch"

    def _json(self, obj, code=200):
        b = db.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        """The request's JSON, or None after answering 413: a body is a short form, never read whole at any size
        (the pod has 128 Mi)."""
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            self.close_connection = True  # its body stays unread
            self._json({"error": "內容太長"}, 413)
            return None
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _user(self):
        return self.headers.get("Cf-Access-Authenticated-User-Email", "")

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/healthz":
            db.one("SELECT 1")
            return self._json({"ok": True})
        if p == "/api/cases":
            return self._json(db.q("SELECT id, subject, kind, status, created_at, analysed_at, summary, engines, round FROM case_ ORDER BY id DESC"))
        m = re.fullmatch(r"/api/cases/(\d+)", p)
        if m:
            r = case_full(int(m[1]))
            return self._json(r) if r else self._json({"error": "沒有這個個案"}, 404)
        if p == "/api/status":
            return self._json({**status(), "me": self._user()})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        p = self.path.split("?")[0]
        b = self._body()
        if b is None:
            return
        if p == "/api/cases":
            subject = (b.get("subject") or "").strip()
            kind = b.get("kind") if b.get("kind") in ("event", "person", "company") else "event"
            engines = [e for e in b.get("engines") or [] if e in pipeline.ENGINES] or list(pipeline.ENGINES)
            if not subject:
                return self._json({"error": "請輸入題目"}, 400)
            return self._json({"id": pipeline.new_case(subject, kind, b.get("context") or "", engines, self._user())})
        m = re.fullmatch(r"/api/cases/(\d+)/(ask|followup|questions|retry|delete|forums|suggest|reanalyse|again)", p)
        if not m:
            return self._json({"error": "not found"}, 404)
        cid, act = int(m[1]), m[2]
        if not db.one("SELECT id FROM case_ WHERE id = %s", cid):
            return self._json({"error": "沒有這個個案"}, 404)
        if act == "ask":
            ids = [int(i) for i in b.get("question_ids") or []]
            db.run("UPDATE question SET selected = (id = ANY(%s)) WHERE case_id = %s AND NOT EXISTS (SELECT 1 FROM ask a WHERE a.question_id = question.id)",
                   ids, cid)
            return self._json({"queued": pipeline.start_asking(cid)})
        if act == "followup":
            return self._json({"queued": pipeline.follow_up(cid, [str(x) for x in b.get("questions") or []])})
        if act == "questions":
            pipeline.rerun_questions(cid)
            return self._json({"ok": True})
        if act == "retry":
            n = 0
            for a in db.q("SELECT id, engine FROM ask WHERE case_id = %s AND status IN ('failed', 'login_needed')", cid):
                db.run("UPDATE ask SET status = 'queued', error = NULL WHERE id = %s", a["id"])
                db.add_task("ask", cid, a["id"], a["engine"])
                n += 1
            if n:
                db.run("UPDATE case_ SET status = 'asking', summary = summary - 'stage' WHERE id = %s", cid)
            return self._json({"queued": n})
        if act == "forums":
            span = b.get("span") if b.get("span") in pipeline.SPANS else "month"
            db.add_task("forums", cid, arg=span)
            return self._json({"queued": span})
        if act == "suggest":
            db.add_task("suggest", cid)
            return self._json({"queued": True})
        if act == "reanalyse":
            pipeline.reanalyse(cid)
            return self._json({"ok": True})
        if act == "again":
            return self._json({"id": pipeline.ask_again(cid, self._user())})
        if act == "delete":
            db.run("DELETE FROM task WHERE case_id = %s", cid)
            db.run("DELETE FROM case_ WHERE id = %s", cid)
            return self._json({"ok": True})

    def log_message(self, fmt, *a):
        if "/healthz" not in (a[0] if a else ""):
            super().log_message(fmt, *a)


def main():
    db.migrate()
    srv = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), H)
    srv.daemon_threads = True
    import signal
    import threading
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    srv.serve_forever()


if __name__ == "__main__":
    main()
