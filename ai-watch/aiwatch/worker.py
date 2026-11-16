"""The worker: takes tasks from the queue (db.take_task) and runs them. Browser tasks (news, ask) share one Chrome, at
most one open question per app at a time in each worker (a person asking one thing after another, not a burst);
model tasks run in threads. Several workers can run: tasks are taken with SKIP LOCKED, and a task whose worker died is
taken again when its lease runs out.

A wall the browser could not pass (engines.Blocked: Cloudflare, Google's /sorry/) usually means the exit IP is the
problem, not the request (2026-10-27: Perplexity walled one worker's IP for every ask while a fresh IP passed at once):
the worker then stops taking browser tasks, lets the open ones finish, writes the logins back and starts its browser
again on a new sticky exit IP.

Stopping (SIGTERM, a rollout): no new task is taken, the running ones finish (terminationGracePeriodSeconds), the
browser's logins are written back, then exit. /healthz on :8081 answers while the loop is alive.
"""
import asyncio
import os
import signal
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, engines, llm, pipeline
from .browser import Browser

ENGINE_SLOTS = 1                 # open questions per app per worker
MODEL_SLOTS = int(os.environ.get("MODEL_SLOTS", "4"))
BROWSER_SLOTS = int(os.environ.get("BROWSER_SLOTS", "4"))
SAVE_EVERY = 600                 # write the browser state back every 10 minutes
MAX_TRIES = 3

state = {"beat": time.time(), "stopping": False, "rotate": False, "why": ""}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = time.time() - state["beat"] < 120
        self.send_response(200 if ok else 503)
        self.end_headers()
        self.wfile.write(b"ok" if ok else b"stuck")

    def log_message(self, *a):
        pass


def _fail(task, e, retry_in):
    """a task that raised: try again later up to MAX_TRIES, then failed (an ask is marked failed too)"""
    msg = f"{type(e).__name__}: {e}"[:1500]
    if task["tries"] < MAX_TRIES and retry_in is not None:
        db.finish_task(task["id"], msg, retry_in)
        return
    db.finish_task(task["id"], msg)
    if task["kind"] == "ask":
        db.run("UPDATE ask SET status = CASE WHEN status = 'login_needed' THEN status ELSE 'failed' END, error = %s WHERE id = %s",
               msg, task["ref"])


def run_model(task):
    try:
        getattr(pipeline, "step_" + task["kind"])(task)
        db.finish_task(task["id"])
    except llm.KeyLimit as e:
        db.engine_status("model", "key_limit", str(e))
        db.finish_task(task["id"], f"KeyLimit: {e}", 3600)  # waits for the limit to reset or be raised; never fails
        return
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        _fail(task, e, 120)
    pipeline.advance(task["case_id"])


async def run_browser(task, b):
    page = await b.page()
    try:
        if task["kind"] == "news":
            await pipeline.step_news(task, page, b.ctx, b.session)
        elif task["kind"] == "forums":
            await pipeline.step_forums(task, page, b.ctx, b.session)
        else:
            await pipeline.step_ask(task, page, b.ctx, b.session)
        db.finish_task(task["id"])
    except engines.LoginNeeded as e:
        db.finish_task(task["id"], f"LoginNeeded: {e}")  # not retried: login.py renews the state, then 重試
    except engines.Blocked as e:
        db.engine_status(task["engine"] or "google", "blocked", str(e))
        state.update(rotate=True, why="a wall: new browser on a new exit IP")
        _fail(task, e, 60)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        try:  # what the page showed, for the error (an app's own message: a limit, a wall, a changed page)
            seen = (await page.evaluate("() => document.body ? document.body.innerText.slice(-400) : ''")).replace("\n", " ")
            e = type(e)(f"{e} | page: {page.url[:80]} {seen}")
        except Exception:  # noqa: BLE001
            pass
        _fail(task, e, 180)
    finally:
        try:
            await page.close()
        except Exception:  # noqa: BLE001
            pass
    pipeline.advance(task["case_id"])


async def main():
    db.migrate()
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", 8081), Health).serve_forever, daemon=True).start()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda: state.update(stopping=True))
    loop.add_signal_handler(signal.SIGINT, lambda: state.update(stopping=True))
    running_b, running_m = {}, set()   # browser: task id → (engine, asyncio task); model: asyncio tasks
    while not (state["stopping"] and not running_m):
        st, loaded_at = db.browser_state(with_time=True)
        async with Browser(st) as b:
            log("browser up, exit session", b.session)
            state["rotate"] = False
            saved = checked = time.time()
            while True:
                state["beat"] = time.time()
                for tid in [t for t, (_, f) in running_b.items() if f.done()]:
                    running_b.pop(tid)
                running_m = {f for f in running_m if not f.done()}
                if (state["stopping"] or state["rotate"]) and not running_b:
                    break
                if not state["stopping"]:
                    while not state["rotate"] and len(running_b) < BROWSER_SLOTS:
                        busy = [e for e, _ in running_b.values() if e]
                        t = await asyncio.to_thread(db.take_task, pipeline.BROWSER_KINDS, busy)
                        if not t:
                            break
                        log("take", t["kind"], t["engine"] or "", t["ref"] or "", "case", t["case_id"])
                        running_b[t["id"]] = (t["engine"], asyncio.create_task(run_browser(t, b)))
                    while len(running_m) < MODEL_SLOTS:
                        t = await asyncio.to_thread(db.take_task, pipeline.MODEL_KINDS)
                        if not t:
                            break
                        log("take", t["kind"], t["ref"] or "", "case", t["case_id"])
                        running_m.add(asyncio.create_task(asyncio.to_thread(run_model, t)))
                if time.time() - checked > 60:   # a new login from login.py: start the browser again with it
                    checked = time.time()
                    newer = await asyncio.to_thread(db.login_saved_at)
                    if newer and (loaded_at is None or newer > loaded_at):
                        state.update(rotate=True, why="a new login: new browser with it")
                if time.time() - saved > SAVE_EVERY and not state["rotate"]:
                    await asyncio.to_thread(db.save_browser_state, await b.state(), "worker", True)
                    saved = time.time()
                await asyncio.sleep(2)
            await asyncio.to_thread(db.save_browser_state, await b.state(),
                                    "worker (stopping)" if state["stopping"] else "worker (new browser)", True)
            if state["rotate"] and not state["stopping"]:
                log(state["why"])
        if state["stopping"]:
            while running_m:   # model tasks need no browser: let them finish
                running_m = {f for f in running_m if not f.done()}
                await asyncio.sleep(1)
    log("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
