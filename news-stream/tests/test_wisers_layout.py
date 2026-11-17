#!/usr/bin/env python3
"""WiseSearch 轉接's row layout (web-app/src/routes/-wisers/, styles/wisers.css), rendered by `vite dev` with the relay's
answers served by Playwright (no relay, no database).

    python3 tests/test_wisers_layout.py        # starts `vite dev` on WISERS_PORT (default 5199)

A saved response (/app/wisers/view/<name>, Deyao 2026-11-17: "just so broken, the layout": its headline, meta, excerpt
and link sat side by side as four squeezed columns, because .wrow was a sideways flex row): in Chromium 1600 px and
WebKit 390 px every row stacks headline → meta → excerpt → 原文 on one left edge, no sideways scroll, no page error.
Signing in: the test's own RSA key signs a token for the WiseSearch Access app (ACCESS_TEST_JWKS in web-app/.dev.vars,
read only in dev; the file is put back as it was at the end). Screenshots in SHOTS.
"""
import json, os, signal, subprocess, time, unittest, urllib.error, urllib.request

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(HERE, "web-app")
PORT = int(os.environ.get("WISERS_PORT", "5199"))
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = os.environ.get("SHOTS") or os.path.expanduser("~/artifacts/wisers-layout")
WISERS_AUD = "af0134321afca2f355d7782cb0ad2a2c06e8f901208d8e8d3bd121941d3e5cab"  # server/access.ts WISERS_AUD
ISS = "https://yyc-hk.cloudflareaccess.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

HIT = "香港科技"
EXCERPT = [["...", False], [HIT, True], ["園公司創新發展商務發展（生命健康科技）高級經理程琪清接受港商記者專訪時表示，立足洽談會交流平台，"
           "兩地生物醫藥科創正迎來雙向賦能的全新發展階段。本屆洽談會上，", False], [HIT, True], ["園攜夥伴企業重點項目亮相…", False]]
ROWS = [("有片丨香港科技園借力京港洽談會 構建生命健康京港雙向科創新格局", "HKCD", 771),
        ("世界頂尖大學校長論壇在港舉行 蔡若蓮：五港校穩佔亞洲十強 突顯國際教育樞紐地位", "Wenweipo Web", 939),
        ("CASHBACK 篤篤賺「雙11」旅遊限時加碼", "Infocast Financial Newswire", 216),
        ("立法會：創新科技及工業局副局長就「貫徹『居家安老為本』，構建長者友善社區」議員議案總結發言（只有中文）— Hongkong Invest",
         "hongkong-invest.com", 1033)]
SAVED = {"crit": {"keyword": "香港AI or 香港人工智能 or 香港科創 or 香港科技"}, "at": "2026-11-17T13:18:00+08:00", "total": 788,
         "items": [dict(doc_id=f"d{i}", headline=h, source=s, page="", kind="website", date=f"2026-11-05T15:{37 - i:02d}:00+08:00",
                        words=w, excerpt=EXCERPT, link="https://example.com/a") for i, (h, s, w) in enumerate(ROWS)]}


def token():
    pem = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    now = int(time.time())
    return jwt.encode({"email": "chendeyao000@gmail.com", "aud": [WISERS_AUD], "iss": ISS, "iat": now, "exp": now + 3600}, pem,
                      algorithm="RS256", headers={"kid": "test"})


class WisersLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(SHOTS, exist_ok=True)
        pub = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
        pub.update(kid="test", alg="RS256", use="sig")
        cls.vars = os.path.join(APP, ".dev.vars")
        cls.before = open(cls.vars).read() if os.path.exists(cls.vars) else None
        base = cls.before if cls.before is not None else open(cls.vars + ".example").read()
        with open(cls.vars, "w") as f:
            f.write("\n".join(l for l in base.splitlines() if not l.startswith("ACCESS_TEST_JWKS=")) +
                    "\nACCESS_TEST_JWKS=" + json.dumps({"keys": [pub]}) + "\n")
        # the page never reads the database; the Hyperdrive binding only needs a well-formed URL
        env = dict(os.environ, CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=os.environ.get(
            "CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE", "postgres://none:none@127.0.0.1:1/none"))
        cls.log = open(os.path.join(SHOTS, "vite.log"), "w")
        cls.srv = subprocess.Popen(["npx", "vite", "dev", "--port", str(PORT), "--strictPort", "--host", "127.0.0.1"], cwd=APP, env=env,
                                   stdout=cls.log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(180):
            try:
                urllib.request.urlopen(BASE + "/app/wisers/", timeout=5)
                break
            except urllib.error.HTTPError:
                break
            except Exception:
                assert cls.srv.poll() is None, "vite dev ended: see " + cls.log.name
                time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        os.killpg(cls.srv.pid, signal.SIGTERM)
        if cls.before is None:
            os.remove(cls.vars)
        else:
            with open(cls.vars, "w") as f:
                f.write(cls.before)

    def test_saved_response_rows_stack(self):
        with sync_playwright() as p:
            for name, kind, vp in (("desktop", p.chromium, {"width": 1600, "height": 1000}), ("phone", p.webkit, {"width": 390, "height": 844})):
                with self.subTest(name):
                    b = kind.launch()
                    page = b.new_page(viewport=vp, extra_http_headers={"Cf-Access-Jwt-Assertion": token()})
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.route("**/app/wisers/api/**", lambda r: r.fulfill(json=SAVED) if "/api/saved/" in r.request.url
                               else r.fulfill(status=404, json={"error": "not in this test"}))
                    page.goto(BASE + "/app/wisers/view/q1.json", timeout=180000)
                    page.wait_for_selector("article.wrow .ex", timeout=180000)
                    rows = page.evaluate("""[...document.querySelectorAll('article.wrow')].map(a => Object.fromEntries(
                      ['h2', '.meta', '.ex', '.lk'].map(s => { const r = a.querySelector(s).getBoundingClientRect(); return [s, [r.top, r.bottom, r.left]]; })))""")
                    self.assertEqual(len(rows), len(ROWS))
                    for i, r in enumerate(rows):
                        self.assertGreaterEqual(r[".meta"][0], r["h2"][1] - 1, f"{name} row {i}: meta beside the headline, not under it")
                        self.assertGreaterEqual(r[".ex"][0], r[".meta"][1] - 1, f"{name} row {i}: excerpt beside the meta")
                        self.assertGreaterEqual(r[".lk"][0], r[".ex"][1] - 1, f"{name} row {i}: 原文 beside the excerpt")
                        for s in (".meta", ".ex", ".lk"):
                            self.assertAlmostEqual(r[s][2], r["h2"][2], delta=1, msg=f"{name} row {i}: {s} off the headline's left edge")
                    self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), f"{name}: sideways scroll")
                    self.assertEqual(errors, [], name)
                    page.screenshot(path=os.path.join(SHOTS, f"saved-{name}.png"), full_page=True)
                    b.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
