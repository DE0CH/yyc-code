"""Feature flags (Deyao, 2026-10-13): OpenFeature, with flagd as the provider.

In the cluster flagd serves news-stream/deploy/apps/flags/flags.json (deploy/apps/flagd.yaml): FLAGD_HOST (set on the
web pods) names it, and the provider keeps the flags in step as the file changes. In a checkout (a test copy, tests)
the same file is read in process. The provider is set up on first use, so processes that never ask never touch it, and a
flag that can't be evaluated counts as OFF: a section that isn't ready never shows by accident. The poller, the MCP
server and the jobs that ask have FLAGD_HOST too, so a flip reaches them without a
redeploy; they ask with no Host (the main site's answer).

  flags.on("search", host)  → bool
  flags.public(host)        → {name: bool} for every flag, for the pages (GET /api/flags)
  flags.off(name)           → None while on, else why it is off (the monitor's "off: …")

The request's Host is the evaluation context: the main site (newswatch.yyc-internal.com) is the simple monitor (stream +
analysis); the beta site (BETA_HOST, its own Access app: Deyao and the people he adds) gets every flag on through the
flags' targeting rule (Deyao, 2026-10-13).
"""
import os, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.environ.get("FLAGS_FILE") or os.path.join(HERE, "deploy", "apps", "flags", "flags.json")  # FLAGS_FILE: tests

# every flag the app asks about (its default lives in flags.json); a new flag gets a line here and an entry there
FLAGS = {"search": "the 搜尋 section (/search, its API and export, the nav entry)",
         "ai": "the AI features: the dashboard's persona button and the AI daily summary (/app/ai/…, /api/ai/…)",
         "reports": "the 報告 section (/reports…, /api/reports)",
         "pictures": "picture analysis: the picture worker's AI reads of post pictures (images.py, Deployment images) and "
                     "the pictures attached to the stream scorer's calls (images.for_scoring); off: nothing scored, the "
                     "scorer text-only, queued pictures stay queued (Deyao, 2026-10-20: cost)",
         "transcription": "transcription by the speech model (media.py, Deployment transcribe; broadcast_fetch.py, CronJob "
                          "broadcast); off: no audio fetched, no model call, queued clips stay queued, a YouTube clip "
                          "still gets its own subtitles (ISP route, no model) (Deyao, 2026-10-20: cost)"}
# why a flag is off, for the monitor and the pages ("off: <why>", never failing)
OFF_WHY = {"pictures": "turned off by Deyao (cost)", "transcription": "turned off by Deyao (cost)"}
MAIN_HOST = "newswatch.yyc-internal.com"  # the main site (moved from newswatch.yyc-internal.com, Deyao, 2026-11-03)
BETA_HOST = "yyc-beta.deyaochen.com"

_client = None
_lock = threading.Lock()


def client():
    global _client
    with _lock:
        if _client is None:
            from openfeature import api
            from openfeature.contrib.provider.flagd import FlagdProvider
            from openfeature.contrib.provider.flagd.config import ResolverType
            if os.environ.get("FLAGD_HOST"):
                api.set_provider(FlagdProvider(host=os.environ["FLAGD_HOST"], port=8013, resolver_type=ResolverType.RPC,
                                               deadline_ms=500))
            elif os.path.exists(FILE):
                api.set_provider(FlagdProvider(resolver_type=ResolverType.IN_PROCESS, offline_flag_source_path=FILE,
                                               port=8013))
            _client = api.get_client("yyc-news-stream")  # no provider: OpenFeature's no-op one, every flag off
            for _ in range(30):  # the provider connects in the background: wait up to 3 s so the first answers are real
                if _client.get_boolean_details(next(iter(FLAGS)), False).error_code is None:
                    break
                time.sleep(0.1)
    return _client


def on(name, host=""):
    try:
        from openfeature.evaluation_context import EvaluationContext
        ctx = EvaluationContext(attributes={"host": (host or "").split(":")[0].lower()})
        return bool(client().get_boolean_value(name, False, ctx))
    except Exception as e:  # noqa: BLE001: a broken flag source means off, never a failed request
        print(f"flag {name}: {e!r}; off", flush=True)
        return False


def off(name):
    """None while the flag is on, else why it is off (OFF_WHY; 'off (flag)' for a flag without a reason)"""
    return None if on(name) else OFF_WHY.get(name, "off (flag)")


def public(host=""):
    return {n: on(n, host) for n in FLAGS}
