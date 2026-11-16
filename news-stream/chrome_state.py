"""A Chrome profile's state kept in Postgres, not on a disk (Deyao, 2026-11-08: state lives in the application, so a job
runs on any node; no Kubernetes volume). Each run starts Chrome on a fresh temporary folder, adds the cookies saved under
kv "chrome:<name>" (the challenges already passed: cf_clearance, Incapsula's, Facebook's datr), and saves the context's
cookies back when it closes. Expired and session cookies are dropped, as Chrome drops them on a restart.

    ctx = chrome_state.launch(p, "browser-profile", channel="chrome", headless=False, no_viewport=True)
    try: ...
    finally: chrome_state.close(ctx)

Every context launch() makes blocks media (nomedia.block: text only, the challenge never blocked; Deyao 2026-10-20).
launch(..., save=False) reads another job's state without writing it (cache and fulltext start from browser-profile's
cookies, as they once copied its folder).
"""
import shutil, tempfile, time

import corpus
import nomedia

_open = {}  # id(ctx) -> (name, folder, save)


def key(name):
    return f"chrome:{name}"


def _live(cookies):
    now = time.time()
    return [c for c in cookies or [] if (c.get("expires") or -1) > now]


def load(name):
    """the saved cookies of <name>"""
    return _live(corpus.kv_get(key(name)))


def save(name, ctx):
    corpus.kv_set(key(name), _live(ctx.cookies()))


def launch(p, name, save=True, **launch_kw):
    """a persistent Chrome context on a fresh folder with <name>'s saved cookies; chrome_state.close(ctx) saves them"""
    folder = tempfile.mkdtemp(prefix=f"chrome-{name}-")
    cookies = load(name)
    ctx = p.chromium.launch_persistent_context(folder, **launch_kw)
    nomedia.block(ctx)  # text only: no picture, video, audio or font (nomedia.py; the challenge is never blocked)
    if cookies:
        try:
            ctx.add_cookies(cookies)
        except Exception as e:  # noqa: BLE001: one malformed cookie must not stop the job; it starts fresh instead
            print(f"chrome_state: {name}: cookies not restored ({e!r:.200})", flush=True)
    _open[id(ctx)] = (name, folder, save)
    return ctx


def close(ctx):
    """save the cookies (unless launched with save=False), close Chrome and remove its folder"""
    name, folder, keep = _open.pop(id(ctx), (None, None, False))
    try:
        if keep:
            try:
                save(name, ctx)
            except Exception as e:  # noqa: BLE001: a crashed browser has no cookies to read; the old ones stay
                print(f"chrome_state: {name}: cookies not saved ({e!r:.200})", flush=True)
    finally:
        try:
            ctx.close()
        finally:
            if folder:
                shutil.rmtree(folder, ignore_errors=True)
