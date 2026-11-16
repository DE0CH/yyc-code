#!/usr/bin/env python3
"""Renders body.html + sources.py into whatsapp.html (published at /news-stream/whatsapp/ by ai-visibility/deploy.sh).
Citations are written ⟦key⟧ in body.html and numbered in first-appearance order; each becomes
<sup><a class="cite" href="#ref-N">N</a></sup> (hover/tap card from ai-visibility/cite-cards.js)."""
import html, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sources import S

here = os.path.dirname(os.path.abspath(__file__))
body = open(os.path.join(here, "body.html")).read()
order = []


def cite(m):
    k = m.group(1)
    if k not in S:
        sys.exit(f"unknown source {k}")
    if k not in order:
        order.append(k)
    n = order.index(k) + 1
    return f'<sup><a class="cite" href="#ref-{n}">{n}</a></sup>'


body = re.sub(r"\s?⟦([a-z0-9-]+)⟧", cite, body)
body = re.sub(r"(</sup>)(<sup>)", r"\1<span class=\"sep\">,</span>\2", body)
unused = [k for k in S if k not in order]
if unused:
    print("unused sources:", unused, file=sys.stderr)

refs = []
for n, k in enumerate(order, 1):
    s = S[k]
    e = {x: html.escape(str(s[x]), quote=True) for x in s}
    who = e["author"] if s["author"] == s["publisher"] else f'{e["author"]} · {e["publisher"]}'
    when = ("accessed " if s["kind"] == "accessed-only" else "updated " if s["kind"] == "updated" else "") + e["date"]
    refs.append(f'<li id="ref-{n}" data-title="{e["title"]}" data-author="{e["author"]}" data-publisher="{e["publisher"]}" '
                f'data-date="{e["date"]}" data-kind="{e["kind"]}" data-type="{e["type"]}" data-url="{e["url"]}">'
                f'<span class="badge b-{s["type"].lower().replace("-", "")}">{e["type"]}</span> '
                f'<a href="{e["url"]}" target="_blank" rel="noopener">{e["title"]}</a>'
                f'<span class="meta">{who} · {when}</span></li>')

page = open(os.path.join(here, "page.html")).read().replace("{{BODY}}", body).replace("{{REFS}}", "\n".join(refs))
out = os.path.join(here, "..", "whatsapp.html")
open(out, "w").write(page)
print(out, len(order), "sources")
