import markdown, re, sys, html, urllib.parse
# Renders the three markdown parts in this folder into report.html (published at /news-stream/ by ai-visibility/deploy.sh).
# Usage: python3 news-stream/docs/render.py   (needs the markdown package)
import os
sp = os.path.dirname(os.path.abspath(__file__)); out = os.path.join(sp, "report.html")
NAMES = ["1-social-search.md", "2-wisersone.md", "3-how-the-clone-works.md"]
parts = [("social", "Daily social search & cost"), ("wisers", "WisersOne"), ("mechanism", "How the clone scrapes & searches")]
refs, body, toc = [], [], []
def cite(m):
    url = m.group(0).rstrip(').,;')
    tail = m.group(0)[len(url):]
    if url not in refs: refs.append(url)
    n = refs.index(url) + 1
    host = urllib.parse.urlparse(url).netloc.replace("www.", "")
    return f'<sup class="ref"><a href="#ref-{n}" title="{html.escape(host)}">{n}</a></sup>{tail}'
for i, (pid, label) in enumerate(parts, 1):
    md = open(os.path.join(sp, NAMES[i - 1])).read()
    md = re.sub(r"\(\s*(https?://[^\s)]+)\s*\)", r" \1 ", md)  # "(url)" → url
    md = re.sub(r"(?m)^((?:  )+)(?=[-*] |\d+\. )", lambda m: "    " * (len(m.group(1)) // 2), md)  # 2-space nesting -> 4
    h = markdown.markdown(md, extensions=["sane_lists"])
    h = re.sub(r"(?<![\"'=>])https?://[^\s<]+", cite, h)
    h = h.replace("<h1>", f'<h1 id="{pid}">', 1)
    h = re.sub(r"<code>([^<]*)</code>", r"<code>\1</code>", h)
    body.append(f'<section class="part">{h}</section>')
    toc.append(f'<a href="#{pid}">{label}</a>')
reflist = "".join(f'<li id="ref-{n}"><a href="{html.escape(u)}" target="_blank" rel="noopener">{html.escape(urllib.parse.urlparse(u).netloc.replace("www.",""))}</a> <span>{html.escape(urllib.parse.unquote(u))}</span></li>' for n, u in enumerate(refs, 1))
page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Social Search Report</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+HK:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root{{--bg:#EEF1F4;--surface:#fff;--ink:#1B2430;--muted:#5F6B7A;--line:#D7DDE4;--bar:#1F2A44;--bar-ink:#E9EEF6;--accent:#2F6FDB;--code:#F2F4F7}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#121722;--surface:#1A2130;--ink:#E6EAF0;--muted:#93A0B2;--line:#2A3344;--bar:#0E1424;--bar-ink:#E6EAF0;--accent:#6E9BEA;--code:#141A27}}}}
:root[data-theme="dark"]{{--bg:#121722;--surface:#1A2130;--ink:#E6EAF0;--muted:#93A0B2;--line:#2A3344;--bar:#0E1424;--bar-ink:#E6EAF0;--accent:#6E9BEA;--code:#141A27}}
*{{box-sizing:border-box}} html,body{{margin:0;background:var(--bg);color:var(--ink)}}
body{{font:16px/1.65 "Noto Sans HK",system-ui,sans-serif}}
header{{background:var(--bar);color:var(--bar-ink);padding:18px 16px}} header .in{{max-width:1180px;margin:0 auto}}
header h1{{margin:0;font-size:20px}} header p{{margin:4px 0 0;color:#9FB0C8;font-size:14px}}
nav.toc{{display:flex;flex-wrap:wrap;gap:8px;padding:12px 16px;max-width:1180px;margin:0 auto}}
nav.toc a{{font-size:14px;padding:4px 12px;border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--ink);text-decoration:none}}
main{{max-width:1180px;margin:0 auto;padding:0 16px 40px}}
.part{{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:6px 18px 14px;margin-bottom:18px;overflow-wrap:anywhere}}
.part h1{{font-size:22px;margin:16px 0 8px;scroll-margin-top:12px}} .part h2{{font-size:18px;margin:22px 0 6px;border-top:1px solid var(--line);padding-top:14px}} .part h3{{font-size:16px;margin:16px 0 4px}}
.part p,.part li{{max-width:78ch}} ul,ol{{padding-left:22px}} li{{margin:3px 0}}
code{{font-size:13px;background:var(--code);padding:1px 4px;border-radius:4px;overflow-wrap:anywhere}}
a{{color:var(--accent)}} sup.ref a{{text-decoration:none;font-size:11px;padding:0 2px}}
.refs ol{{font-size:13px;color:var(--muted)}} .refs li span{{display:block;overflow-wrap:anywhere;font-size:12px}}
@media (min-width:1000px){{.wrap{{display:grid;grid-template-columns:230px 1fr;gap:20px;max-width:1180px;margin:0 auto;padding:16px}} nav.toc{{flex-direction:column;position:sticky;top:16px;align-self:start;padding:0}} main{{padding:0}} nav.toc a{{border-radius:8px}}}}
</style></head><body>
<header><div class="in"><h1>YYC news stream: social search, WisersOne, and how the clone works</h1><p>2026-10-13 · compiled by Claude for Deyao</p></div></header>
<div class="wrap"><nav class="toc">{"".join(toc)}<a href="#refs">Sources</a></nav>
<main>{"".join(body)}<section class="part refs" id="refs"><h1>Sources</h1><ol>{reflist}</ol></section></main></div>
</body></html>'''
open(out, "w").write(page)
print(len(refs), "refs")
