#!/usr/bin/env python3
"""The call log of YYC's data API (yyc-mcp, news-stream/mcp-edge), for the access catalogue's admins:

    infra/mcp-access.py audit [N] [EMAIL]

Needs YYC_MCP_TOKEN (store `yyc`: Deyao's service token). The same log as the /admin page. Who may use the API is
YYC's access catalogue (its `users`; infra/catalogue.sh at the repo's root), not this script.
"""
import json, os, sys, urllib.parse, urllib.request

BASE = os.environ.get("YYC_MCP_URL", "https://mcp.yyc-internal.com")


def call(path):
    req = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + os.environ["YYC_MCP_TOKEN"],
                                                       "User-Agent": "mcp-access/2"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main(a):
    if a and a[0] == "audit" and len(a) <= 3:
        q = {"limit": a[1] if len(a) > 1 else "50"}
        if len(a) > 2:
            q["email"] = a[2]
        for c in call("/admin/api/audit?" + urllib.parse.urlencode(q))["calls"]:
            print(f"{c['at'][:19]} {c['email']:32} {c['via']:4} {c['tool']:20} {c['status']} {c['ms']} ms {c.get('client') or ''}")
    else:
        print(__doc__)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
