"""Live check that a deploy reaches open pages on newswatch.yyc-internal.com (web-app/README.md "Updates reach open pages").

    python3 tests/live_ui_update.py webkit|chromium     # then deploy (push anything under news-stream/web-app/)

Opens 串流 three times (visible and scrolled; hidden; then left for 數據分析 so Back can restore it), prints
READY_FOR_DEPLOY, waits up to 10 minutes for /version to change, and prints JSON: how long the visible page took to
know of the new build, that no page was reloaded (each tab's documents with their navigation type, the same JS context,
the scroll kept; the hidden page also after 60 s and back on screen, its data reads answering 200), that the next link
was a full load of the new build, and Back (Chromium: started by the script, so its back/forward cache is on; the
restored page stays, its next link loads the new build).
Screenshots: ~/artifacts/spa/ui-update/live/."""
import json, os, re, subprocess, sys, tempfile, time, urllib.request  # noqa: E401
from playwright.sync_api import sync_playwright

ENGINE = sys.argv[1]
SITE = "https://newswatch.yyc-internal.com"
OUT = os.path.expanduser("~/artifacts/spa/ui-update/live")
os.makedirs(OUT, exist_ok=True)
VIS = """(() => { let forced = null; const own = Object.getOwnPropertyDescriptor(Document.prototype, "visibilityState").get;
Object.defineProperty(document, "visibilityState", { configurable: true, get() { return forced ?? own.call(document); } });
window.__setVisibility = v => { forced = v; document.dispatchEvent(new Event("visibilitychange")); };
addEventListener("pageshow", e => { try { if (e.persisted) sessionStorage.setItem("t-bf", "1"); } catch {} });
try { const a = JSON.parse(sessionStorage.getItem("t-docs") || "[]"), n = performance.getEntriesByType("navigation")[0];
  a.push([location.pathname, n ? n.type : ["navigate", "reload", "back_forward"][performance.navigation.type] || "?"]); sessionStorage.setItem("t-docs", JSON.stringify(a)); } catch {}
new MutationObserver(() => { if (document.documentElement.dataset.update === "ready" && !window.__readyAt) window.__readyAt = Date.now(); })
  .observe(document, { attributes: true, subtree: true, attributeFilter: ["data-update"] }); })();"""


def version():
    try:
        return _version()
    except Exception:  # noqa: BLE001
        return ""


def _version():
    return urllib.request.urlopen(urllib.request.Request(SITE + "/version", headers={"User-Agent": "Mozilla/5.0 yyc-check"}), timeout=10).read().decode().strip()


with sync_playwright() as pw:
    proc = None
    if ENGINE == "webkit":
        b = pw.webkit.launch()
        mk = lambda: b.new_context(**pw.devices["iPhone 13"])
    else:
        port = 9444
        proc = subprocess.Popen([pw.chromium.executable_path, "--headless=new", f"--remote-debugging-port={port}",
                                 f"--user-data-dir={tempfile.mkdtemp()}", "--no-first-run", "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                b = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}"); break
            except Exception:
                time.sleep(0.5)
        mk = lambda: b.new_context(viewport={"width": 1600, "height": 1000})
    def open_page(name, path, y):
        """a page on the current build, scrolled to y (tried again when it reloads itself meanwhile: a page served by an
        edge that was still on the previous build updates itself at once)"""
        for _ in range(3):
            try:
                ctx = mk(); ctx.set_default_timeout(30000); ctx.add_init_script(VIS)
                p = ctx.new_page(); p.goto(SITE + path); p.wait_for_load_state("networkidle")
                p.wait_for_function("new URLSearchParams(location.search).has('p')")
                time.sleep(5)
                for _ in range(10):
                    p.evaluate("y => scrollTo(0, y)", y); time.sleep(0.8)
                    if abs(p.evaluate("scrollY") - y) < 2: break
                p.evaluate("window.__doc = 1")
                return p, p.url, p.evaluate("scrollY")
            except Exception as e:  # noqa: BLE001
                print(name, "set up again:", str(e)[:100], flush=True)
        raise RuntimeError(name + " could not be set up")

    pages = {name: open_page(name, path, y) for name, path, y in (("visible", "/", 700), ("hidden", "/", 1200), ("back", "/", 500))}
    pages["back"][0].goto(SITE + "/dashboard"); pages["back"][0].wait_for_load_state("domcontentloaded")
    pages["hidden"][0].evaluate("window.__setVisibility('hidden')")
    old = version()
    print("pages open on", old, flush=True)
    print("READY_FOR_DEPLOY", flush=True)
    # the caller pushes a deploy now; wait for /version to change (up to 10 min)
    t_end = time.time() + 600
    while time.time() < t_end:  # a new 12-character id, answered twice in a row (not one stray answer)
        v1 = version()
        if v1 != old and re.fullmatch(r"[0-9a-f]{12}", v1) and version() == v1:
            break
        time.sleep(0.5)
    t0 = time.time(); new = v1
    print("live:", new, flush=True)
    res = {"engine": ENGINE, "from": old, "to": new}
    docs = lambda pg: pg.evaluate("JSON.parse(sessionStorage.getItem('t-docs') || '[]')")
    v, vurl, vy = pages["visible"]
    v.wait_for_function("document.documentElement.dataset.update === 'ready'", timeout=30000)
    res["visible_knew_after_s"] = round(v.evaluate("window.__readyAt") / 1000 - t0, 2)
    print("visible knew after", res["visible_knew_after_s"], "s", flush=True)
    h, hurl, hy = pages["hidden"]
    reads = []
    h.on("response", lambda r: "/_serverFn/" in r.url and reads.append(r.status))
    time.sleep(60)  # the old code reloaded a hidden page within seconds
    res["visible_kept"] = {"same_doc": v.evaluate("window.__doc") == 1, "scroll": [v.evaluate("scrollY"), vy],
                           "docs": docs(v), "note": v.locator(".yyc-update").count()}
    res["hidden_kept"] = {"same_doc": h.evaluate("window.__doc") == 1, "docs": docs(h)}
    h.evaluate("window.__setVisibility('visible')")
    time.sleep(10)
    res["hidden_on_screen"] = {"same_doc": h.evaluate("window.__doc") == 1, "docs": docs(h), "scroll": [h.evaluate("scrollY"), hy],
                               "build": h.evaluate("document.documentElement.dataset.build"), "reads": reads,
                               "note": h.locator(".yyc-update").count()}
    v.screenshot(path=f"{OUT}/{ENGINE}-visible-kept.png")
    v.locator("a[href^='/dashboard']:visible").first.click()
    v.wait_for_function(f"document.documentElement.dataset.build === '{new}'", timeout=30000)
    res["link_full_load_to_new"] = {"new_doc": v.evaluate("window.__doc") is None, "url": v.url, "docs": docs(v)}
    bk, burl, by = pages["back"]
    bk.go_back(wait_until="commit")
    bk.wait_for_function("document.readyState === 'complete'", timeout=30000)
    time.sleep(3)
    res["back"] = {"url_kept": bk.url == burl, "bfcache": bk.evaluate("sessionStorage.getItem('t-bf')"),
                   "build": bk.evaluate("document.documentElement.dataset.build"), "docs": docs(bk)}
    if bk.evaluate("document.documentElement.dataset.build") != new:
        bk.locator("a[href^='/dashboard']:visible").first.click()
        bk.wait_for_function(f"document.documentElement.dataset.build === '{new}'", timeout=30000)
        res["back"]["next_link"] = {"url": bk.url, "docs": docs(bk)}
    print(json.dumps(res), flush=True)
    b.close()
    if proc: proc.kill()
