"""The analysis model (Deyao, 2026-10-27: DeepSeek V4.1 Flash) on OpenRouter, with the app's own key `yyc-aiwatch`
(OPENROUTER_API_KEY; USD 30 a calendar month, a safety stop: EXPENSES.md). Every call is logged with OpenRouter's own
cost (usage.cost) in llm_call. Replies are JSON (response_format json_object); a reply that isn't valid JSON is
repaired (json_repair) and, when that fails too, asked again (three tries). A spent key (HTTP 402/403 "limit") raises KeyLimit: the task waits and the case page says so.
"""
import json
import os
import time
import urllib.error
import urllib.request

import json_repair

from . import db

MODEL = os.environ.get("AIWATCH_MODEL", "deepseek/deepseek-v4.1-flash-1017")
API = "https://openrouter.ai/api/v1/chat/completions"


class KeyLimit(Exception):
    pass


def _post(body, timeout=240):
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "Content-Type": "application/json",
        "HTTP-Referer": "https://aiwatch.yyc-internal.com", "X-Title": "YYC AI watch"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")[:500]
        if e.code in (402, 403) and ("limit" in msg.lower() or "credit" in msg.lower()):
            raise KeyLimit(msg) from e
        raise RuntimeError(f"OpenRouter HTTP {e.code}: {msg}") from e


def ask_json(system, user, step, case_id=None, reasoning="low", max_tokens=24000):
    """→ the reply parsed as JSON (an object)"""
    body = {"model": MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"}, "max_tokens": max_tokens, "usage": {"include": True},
            "reasoning": {"effort": reasoning}}
    last = None
    for attempt in range(3):
        try:
            r = _post(body)
        except KeyLimit:
            raise
        except Exception as e:  # noqa: BLE001 — a transient error: try again
            last = e
            time.sleep(5 * (attempt + 1))
            continue
        u = r.get("usage") or {}
        db.run("INSERT INTO llm_call (case_id, step, model, prompt_tokens, completion_tokens, cost_usd) VALUES (%s, %s, %s, %s, %s, %s)",
               case_id, step, r.get("model") or MODEL, u.get("prompt_tokens"), u.get("completion_tokens"), u.get("cost"))
        choice = (r.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        if choice.get("finish_reason") == "length":
            # the reasoning and the reply share max_tokens: a long grouping ran out before its JSON (2026-10-27)
            body["max_tokens"] = min(64000, body["max_tokens"] * 2)
            last = RuntimeError(f"reply cut at {u.get('completion_tokens')} tokens")
            continue
        body_ = text[text.find("{"): text.rfind("}") + 1]
        try:
            return json.loads(body_)
        except ValueError as e:
            last = e
        # the model's JSON is now and then a comma or a quote short (seen in long Chinese replies): repaired, it
        # still holds everything asked for
        try:
            fixed = json_repair.loads(body_)
            if isinstance(fixed, dict) and fixed:
                return fixed
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"no JSON reply for {step}: {last}")
