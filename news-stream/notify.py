"""Messages to Deyao from the app: a Discord webhook (env DISCORD_WEBHOOK, Secret app-env) into the `yyc-alerts`
channel of his Discord server. Used only when something needs him: a provider's hard stop (ai.LimitReached: the
OpenRouter key's limit or credit). Plain text, calm tone, never a secret. Without DISCORD_WEBHOOK the message is
only logged (the monitor still shows the state)."""
import json, os, urllib.request


def deyao(text):
    url = os.environ.get("DISCORD_WEBHOOK")
    if not url:
        print("notify: DISCORD_WEBHOOK not set; not sent:", text[:300], flush=True)
        return False
    req = urllib.request.Request(url, data=json.dumps({"content": text[:1990]}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "yyc-news-stream/1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print("notify: Discord failed:", repr(e)[:200], flush=True)
        return False
