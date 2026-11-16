"""A fake WisersOne for the relay's tests (test_wisers_relogin.py, test_wisers_rollout.py, test_wisers_page.py).

Two local servers, as Wisers has: "home" (/wevo/home and /wevo/api: a valid session cookie, or a 302 to the login
page on the other origin, as Wisers does) and "login" (the login form with Wisers' field classes, an optional image
captcha, one session per user: a login revokes every earlier session; S["config_hang"] = N: the next
getUserCustomConfig answers after N s; S["api_401"]: an over session's /wevo/api calls
answer HTTP 401 with an empty body instead of the 302, as WisersOne did on 2026-11-06; like Wisers', the login page long-polls
/api/listenUserScan without a pause, so its network never goes idle). /wevo/api answers the paths the relay uses:
search/searchresult (items made from the keyword) and every other path of wisers/paths.py (option lists, label trees,
media, checks, the derived calls, documents, same page, the image quota gate, the download), plus /getFile/… images (two
per document) and POST /wevo/download/downloadDocuments (a text file of the documents the session was given). Its home
page carries a Google Tag Manager script, as WisersOne's does (the relay must never load it). Documents whose id starts
with "q60003" answer the document quota code, "q60004" the image one; S["paths"] counts every call per path.
Keywords that change how a search answers: "slow…" takes 17 s, "slowN…" N seconds (e.g. slow90: 90 s, a big real
search), "boom…" Wisers' error code, "drop…" the connection closes without an answer (counted as a search first, as
Wisers would), "relog…" answers E40001 the first time (Wisers' own "no login record" code: the session is over, the search not run). S["searches"]
counts every search Wisers ran (= what the account's quota would see).

    w = FakeWisers(captcha=True, latency=0.0); w.home_url, w.login_url; w.S["logins"], w.S["max_inflight"]

The relay keeps its state in Postgres (wisers/store.py): the tests need TEST_DATABASE_URL, and each test uses a store
namespace of its own (new_ns(); Store(ns) reads and writes it), so nothing touches another test's or the live relay's rows.
"""
import json, os, re, secrets, sys, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or sys.exit(
    "TEST_DATABASE_URL is not set (the WiseSearch relay's state is in Postgres: wisers/store.py)")
_NS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (_NS_DIR, os.path.join(_NS_DIR, "wisers")):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import store as wstore  # noqa: E402


def new_ns(name):
    """a store namespace for one test (the relay's WISERS_STORE_NS)"""
    return f"test-{name}-{secrets.token_hex(3)}"


class Store:
    """a test's view of one store namespace: write/read/read_json/write_json/exists/lines/append/listdir/mtime"""
    def __init__(self, ns):
        self.ns = ns

    def __getattr__(self, name):
        fn = getattr(wstore, name)

        def call(*a, **k):
            old, wstore.NS = wstore.NS, self.ns
            try:
                return fn(*a, **k)
            finally:
                wstore.NS = old
        return call

def _png(w=120, h=160):
    """a valid grey PNG (a newspaper page stand-in), pure stdlib"""
    import struct, zlib
    raw = b"".join(b"\x00" + bytes([200 if (y // 8) % 2 else 235]) * w for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


PNG = _png()
SOLVED = "AbC9"
CREDS = {"WISERS_GROUP_ID": "grp", "WISERS_USER_ID": "usr", "WISERS_PASSWORD": "pw-ok"}

FORM = """<html><body><form method="post" action="/submit">
<input class="NewInputField__Input-sc-1j8023t-2" name="g"><input class="NewInputField__Input-sc-1j8023t-2" name="u">
<input class="NewInputField__Input-sc-1j8023t-2" name="p" type="password">
{captcha}<input type="submit" value="Login"></form>
<script>(async () => {{ for (;;) {{ try {{ await fetch("/api/listenUserScan?uuid=qr1"); }} catch (e) {{}} }} }})();</script>
</body></html>"""
CAPTCHA = ('<img class="CaptchaField__CaptchaImage-sc-1" src="/captcha.png" width="80" height="30" '
           'onclick="this.src=\'/captcha.png?\'+Date.now()"><input class="CaptchaField__Input-sc-2" name="c">')

MEDIA = {"typeList": [{"text": "報刊", "value": "print", "items": [{"text": "香港報刊", "value": "print-hk"}]},
                      {"text": "網絡", "value": "web"}],
         "categoryList": [{"text": "新聞", "value": "news"}], "regionList": [{"text": "香港", "value": "hk"}]}
NEGATIVE = {"keywordList": [{"text": "罪案", "value": "crime"}]}


def items_for(keyword, start, n=3):
    return [{"headline": f"{keyword} 報道 {start + i}", "source": "明報", "publishDate": "2026-10-16 08:00", "author": "記者",
             "excerpt": f'今日 <font class="highlight">{keyword}</font> 消息 <b>x</b>', "typeId": "print" if i == 0 else "web-site",
             "url": "" if i == 0 else f"https://news.example.com/a{start + i}?utm_source=x&id={i}", "docId": f"doc{start + i}",
             "pubId": "p1", "pubCode": "MP", "pubType": "P" if i == 0 else "U", "clusterAttrKey": "c1" if i < 2 else "c2",
             "wordcount": 300 + i, "pageNo": "A07" if i == 0 else None, "section": "財經" if i == 0 else "新聞",
             "columnName": "一語千金" if i == 0 else None, "circulation": "75000" if i == 0 else None,
             "displayStyle": {"hasImg": i == 0, "hasPdf": i == 0, "hasText": True, "hasVideo": False, "hasAudio": False,
                              "hasWebsite": i != 0}, "imageCount": None,
             "extraField": "extra", "emptyField": ""} for i in range(n)]


def document(doc_id):
    """search/getDocumentByDocId's answer"""
    if doc_id.startswith("q60003"):
        return {"returnCode": "60003", "quotaLimitCode": "60003", "item": {}}
    return {"returnCode": "50000", "quotaLimitCode": "", "item": {
        "docId": doc_id, "headline": f"文章 {doc_id}", "source": "明報", "publishDate": "2026-10-16 08:00", "pageNo": "A07",
        "content": f'<p>第一段 <font class="highlight">全文</font> {doc_id}</p><p onclick="x()">第二段<script>bad()</script></p>',
        "imageCount": "2", "intDocId": "12345", "wordcount": "300", "displayType": "0"}}


class FakeWisers:
    def __init__(self, captcha=True, latency=0.0):
        self.captcha, self.latency, self.page_items = captcha, latency, 3
        self.S = {"valid": set(), "logins": 0, "tries": 0, "searches": 0, "inflight": 0, "max_inflight": 0, "calls": [], "relogged": set(),
                  "polls": 0, "paths": {}, "files": [], "download": None, "bodies": [], "api_401": False,
                  "config_hang": 0}
        self.lock = threading.Lock()
        fake = self

        class Home(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def signed_in(self):
                c = self.headers.get("Cookie") or ""
                return any(p.strip().startswith("sess=") and p.strip()[5:] in fake.S["valid"] for p in c.split(";"))

            def out(self, code, body, ctype, headers=()):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in headers:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self.signed_in():
                    return self.out(302, b"", "text/plain", [("Location", fake.login_url)])
                m = re.match(r"/getFile/(getThumbnailImageByDocId|getImageByDocId)/([^/]+)/(\d+)$", self.path)
                if m:
                    fake.S["files"].append(self.path)
                    return self.out(200, PNG, "image/png") if int(m.group(3)) <= 2 else self.out(404, b"", "text/plain")
                self.out(200, b'<html><head><script async src="https://www.googletagmanager.com/gtm.js?id=GTM-5VC7489"></script>'
                              b'</head><body>WisersOne home</body></html>', "text/html")

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if not self.signed_in() and fake.S["api_401"] and self.path == "/wevo/api":
                    return self.out(401, b"", "text/plain")  # WisersOne on 2026-11-06: an over session's calls, no redirect
                if not self.signed_in():
                    return self.out(302, b"", "text/plain", [("Location", fake.login_url)])
                if self.path == "/wevo/download/downloadDocuments":
                    g = fake.S["download"] or {}
                    return self.out(200, ("\n".join(g.get("docIds") or ["(nothing)"]) + "\n").encode(), "text/plain")
                body = json.loads(raw or b"{}")
                fake.S["bodies"].append(body)
                g0 = body["requests"]["g0"]
                if g0["resource"] == "SessionService":
                    fake.S["download"] = g0["params"].get("downloadDocument")
                    fake.S["paths"]["downloadDocument"] = fake.S["paths"].get("downloadDocument", 0) + 1
                    return self.out(200, json.dumps({"g0": {"data": {"ok": True}}}).encode(), "application/json")
                with fake.lock:
                    fake.S["inflight"] += 1
                    fake.S["max_inflight"] = max(fake.S["max_inflight"], fake.S["inflight"])
                try:
                    p = body["requests"]["g0"]["params"]
                    path, c = p["path"], p.get("criteria") or {}
                    fake.S["calls"].append(path)
                    fake.S["paths"][path] = fake.S["paths"].get(path, 0) + 1
                    if path == "search/searchresult":
                        kw, start = c.get("keyword", ""), int(c.get("startIdx") or 1)
                        if kw.startswith("relog") and kw not in fake.S["relogged"]:  # Wisers' "no login record": not run
                            fake.S["relogged"].add(kw)
                            fake.S["valid"] = set()  # that session is over
                            return self.out(200, json.dumps({"g0": {"data": {"code": "E40001", "message": "no login record"}}}).encode(),
                                            "application/json")
                        m = re.match(r"slow(\d+)", kw)
                        time.sleep(int(m.group(1)) if m else 17 if kw.startswith("slow") else fake.latency)
                        fake.S["searches"] += 1
                        if kw.startswith("drop"):  # the connection breaks after Wisers ran the search
                            self.close_connection = True
                            self.connection.shutdown(2)
                            return
                        if kw.startswith("boom"):  # "boom…": Wisers answers an error code
                            return self.out(200, json.dumps({"g0": {"data": {"code": "40001", "message": "bad criteria"}}}).encode(),
                                            "application/json")
                        data = {"items": items_for(kw, start, fake.page_items), "total": "45", "totalDocCount": "50", "maxDocs": "2000",
                                "docCount": "45", "mediaCount": None, "mediaType": "", "searchCriteriaId": "sc1",
                                "progress": {"progress": "1/1", "percent": "100%", "finished": True}, "criteriaEcho": c}
                    elif path == "search/mediamessage":
                        data = MEDIA
                    elif path == "getNegativeKeyWords":
                        data = NEGATIVE
                    elif path.startswith("label/labels/"):
                        node = path.rsplit("/", 1)[1]
                        data = {"subRoots": [{"labelId": f"{node}-{i}", "entityId": f"{node}{i}", "labelTypeId": "7" if node != "0" and node != "35" else "",
                                              "name_hk": f"標籤 {node}-{i}", "childrenSize": 1 if node in ("0", "35") else 0} for i in range(2)]}
                    elif path == "getUserCustomConfig":
                        if fake.S["config_hang"]:  # S["config_hang"] = N: the next call answers only after N s (a hang)
                            n, fake.S["config_hang"] = fake.S["config_hang"], 0
                            time.sleep(n)
                        data = {"enableHotPick": "true", "enableReport": "false", "maxSearchYears": "5"}
                    elif path == "getHotPicks":
                        data = {"hotPickList": [{"id": "h1", "name": "施政報告"}]}
                    elif path == "search/keywords":
                        data = {"autoComplete": json.dumps([c.get("keyword", "") + "報告", c.get("keyword", "") + "新聞"], ensure_ascii=False)}
                    elif path.startswith("label/labelautocomplete") or path == "label/autocomplete":
                        data = {"labelList": [{"labelId": "L9", "labelTypeId": "7", "name_hk": "建議 " + (c.get("inputStr") or "")}]}
                    elif path == "label/getLabelNameList":
                        data = {"labelList": [{"labelId": x, "name_hk": "名 " + x} for x in (c.get("inputStr") or "").split(",") if x]}
                    elif path == "media":
                        data = {"mediaList": [{"id": "p1", "name": "明報", "type": "print"}], "total": 1}
                    elif path == "medianame":
                        data = {"mediaList": [{"id": i, "text": "媒體 " + i} for i in c.get("ids") or []]}
                    elif path.startswith("getPublication/"):
                        data = {"id": path.split("/")[1], "name": "明報", "frequency": "日報", "region": "香港"}
                    elif path == "search/validateKeywords" and "清單" in (c.get("keyword") or ""):
                        data = ["清單"]  # an answer that is not an object (the live relay crashed on one: 2026-10-29)
                    elif path == "search/validateKeywords":
                        data = {"illegalChar": [], "stop-keyword": ["的"] if "的" in (c.get("keyword") or "").split() else []}
                    elif path == "validateSearchDate":
                        data = {"validResultList": [{"valid": "true"}]}
                    elif path == "search/getMediaTypeCount":
                        data = {"mediaCount": {"all": 45, "print": 10, "web": 30, "social": 5, "wlforum": 0, "wlblog": 0}}
                    elif path in ("search/getSearchResultFilter", "search/getSearchResultMatchFilter") or path.startswith("label/resultfilter/"):
                        data = {"list": [{"text": "香港", "value": "hk", "count": 12}], "criteriaEcho": c}
                    elif path.startswith("label/newscount/"):
                        data = {"count": 7}
                    elif path in ("search/getHotKeywordsReport", "search/getHotNegativeWords"):
                        data = {"list": [{"keyword": "施政", "count": 9}, {"keyword": "房屋", "count": 4}]}
                    elif path == "search/getSimilarDocuments":
                        data = {"items": items_for("相似", 1, 2), "criteriaEcho": c}
                    elif path == "search/getExcerptByDocId":
                        data = {"excerpt": f"摘要 {c.get('docId')}"}
                    elif path == "search/searchMergeCount":
                        data = [{"clusterAttrKey": k, "count": 2} for k in c.get("cluster_ids") or []]
                    elif path == "search/getDocumentByDocId":
                        data = document(c.get("docId") or "")
                    elif path == "search/getSamePageDocs":
                        data = {"same_page_docs": [{"docId": "sp1", "headline": "同版文章一"}, {"docId": c.get("docId"), "headline": c.get("headline")}]}
                    elif path in ("search/checkQuotaExceed", "search/writeClickLogByClickType"):
                        data = {"returnCode": "60004" if str(c.get("docId", "")).startswith("q60004") else "50000", "quotaLimitCode": ""}
                    elif path == "validateDownloadQuota":
                        data = {"returnCode": "50000"}
                    else:
                        data = {}
                    self.out(200, json.dumps({"g0": {"path": path, "data": {"code": "50000", "path": path, "data": data}}},
                                             ensure_ascii=False).encode(), "application/json")
                finally:
                    with fake.lock:
                        fake.S["inflight"] -= 1

        class Login(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/api/listenUserScan"):  # the WeChat QR long-poll: the network is never idle
                    fake.S["polls"] += 1
                    time.sleep(1.5)
                    return self.out_json(b'{"status": "waiting"}')
                png = self.path.startswith("/captcha.png")
                b = PNG if png else FORM.format(captcha=CAPTCHA if fake.captcha else "").encode()
                self.send_response(200)
                self.send_header("Content-Type", "image/png" if png else "text/html")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def out_json(self, b):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                try:
                    self.wfile.write(b)
                except OSError:  # the page went on (the login) while a poll was open
                    pass

            def do_POST(self):
                f = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
                fake.S["tries"] += 1
                g = lambda k: (f.get(k) or [""])[0]
                if (g("g"), g("u"), g("p")) == ("grp", "usr", "pw-ok") and (not fake.captcha or g("c") == SOLVED):
                    fake.S["logins"] += 1
                    tok = secrets.token_hex(8)
                    fake.S["valid"] = {tok}  # one session per user
                    self.send_response(302)
                    self.send_header("Set-Cookie", f"sess={tok}; Path=/")  # host-only on 127.0.0.1: both servers
                    self.send_header("Location", fake.home_url)
                    self.send_header("Content-Length", "0")
                    return self.end_headers()
                self.do_GET()

        self.home = ThreadingHTTPServer(("127.0.0.1", 0), Home)
        self.login = ThreadingHTTPServer(("127.0.0.1", 0), Login)
        self.home_url = f"http://127.0.0.1:{self.home.server_port}/wevo/home"
        self.login_url = f"http://127.0.0.1:{self.login.server_port}/?product=wiseone"
        for s in (self.home, self.login):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    def env(self, ns):
        """the relay's environment against this fake, its state in store namespace ns"""
        return dict(CREDS, WISERS_HOME=self.home_url, WISERS_LOGIN_URL=self.login_url, WISERS_STORE_NS=ns,
                    DATABASE_URL=os.environ["DATABASE_URL"])

    def stale_state(self, ns):
        """a saved session (state.json in namespace ns) Wisers no longer accepts"""
        Store(ns).write_json("state.json", {"cookies": [{"name": "sess", "value": "stale", "domain": "127.0.0.1", "path": "/",
                                                         "expires": -1, "httpOnly": False, "secure": False, "sameSite": "Lax"}],
                                            "origins": []})
