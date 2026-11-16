#!/usr/bin/env python3
"""Can a cheaper model do stage 1 (Deyao, 2026-10-28: "swap a non-frontier and not thinking model (not from Google,
Anthropic or OpenAI) for stage one")? Each candidate scores the same posts with the live stage-1 prompt
(streams.score_prompt, the current briefs, no thinking) in batches of streams.BATCH, and is compared with what the
live pipeline decided: a pair whose final (stage-2) score reached 50 must be screened in (>= streams.SCREEN) or the
item is lost; every pair screened in costs a stage-2 read. The live stage-1 model runs too, as the control on the same prompt.

Input: a JSONL sample of posts with their live scores (stratum A: a final score >= 50, B: screened in but rejected by
stage 2, C: screened out), exported from YYC's Postgres. Nothing is written to YYC's database; each call's cost is
OpenRouter's own usage.cost. Resumable: the state file keeps every answer.

    python3 tools/stage1_trial.py run --sample sample.jsonl --state trial.json --models qwen/qwen3.7-flash,deepseek/deepseek-v4.1-flash
    python3 tools/stage1_trial.py report --sample sample.jsonl --state trial.json
"""
import argparse, json, os, random, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ai, streams  # noqa: E402

PURPOSE = "scoring_test"
LOCK = threading.Lock()
# open-weight models are served by many providers: the cheapest fp8-or-better endpoint outside the mainland providers,
# whose filters sit in front of the model (Deyao, 2026-10-28: "it's usually not the model but a thing in front of
# it"); a model only its maker serves goes there
OPEN_WEIGHT = {"quantizations": ["fp8", "fp16", "bf16", "fp32", "unknown"], "sort": "price",
               "ignore": ["alibaba", "streamlake", "siliconflow", "baidu"]}
SOLE_PROVIDER = ("qwen/qwen3.7-flash", "mistralai/")


def load_streams():
    with open(os.path.join(os.path.dirname(streams.__file__), "streams.json")) as f:
        d = json.load(f)
    return [s for s in (d if isinstance(d, list) else d["streams"]) if s.get("brief")]  # the split children have none


def batches(posts):
    ps = posts[:]
    random.Random(7).shuffle(ps)  # strata mixed, as live batches are
    return [ps[i:i + streams.BATCH] for i in range(0, len(ps), streams.BATCH)]


def call(model, sts, batch):
    """one stage-1 call → (answer {post id: {stream: score}} or None, usd, tokens in, tokens out, error)"""
    prompt = streams.score_prompt(sts, batch)
    body_reasoning = ai.LEAST_THINKING if model.startswith(ai.THINKING_MANDATORY) else ai.NO_THINKING
    cost = {}
    real = ai._record

    def record(purpose, model_, messages, u, sink=None, generation=None):
        cost.update(usd=float(u.get("cost") or 0), tin=u.get("prompt_tokens"), tout=u.get("completion_tokens"),
                    cached=(u.get("prompt_tokens_details") or {}).get("cached_tokens"))
    ai._record = record
    t0 = time.time()
    try:
        res = ai.jsonc(ai.Prompt(prompt, streams.cached_chars(sts, prompt)), PURPOSE, model=model,
                       max_tokens=streams.SCORE_TOKENS, reasoning=body_reasoning, timeout=300,
                       **({} if model.startswith(SOLE_PROVIDER) else {"provider": OPEN_WEIGHT}))
        err = None
    except Exception as e:  # noqa: BLE001: recorded per batch
        res, err = None, repr(e)[:300]
    finally:
        ai._record = real
    out = None
    if res is not None:
        out = {}
        ids = {s["id"] for s in sts}
        for t in res.get("posts", []):
            i = t.get("i")
            if isinstance(i, int) and 0 <= i < len(batch):
                got = {}
                for x in t.get("streams") or []:
                    try:
                        if x.get("id") in ids:
                            got[x["id"]] = max(0, min(100, int(x.get("score"))))
                    except (TypeError, ValueError):
                        pass
                out[str(batch[i]["id"])] = got
    return out, cost.get("usd", 0.0), cost.get("tin"), cost.get("tout"), err, round(time.time() - t0, 1)


def run(a):
    posts = [json.loads(l) for l in open(a.sample)]
    sts = load_streams()
    bs = batches(posts)
    state = json.load(open(a.state)) if os.path.exists(a.state) else {}
    if a.batches:
        bs = bs[:a.batches]

    def one(model, k, b):
        key = f"{model}#{k}"
        if key in state and state[key]["answer"] is not None:
            return
        ans, usd, tin, tout, err, secs = call(model, sts, b)
        with LOCK:
            state[key] = {"answer": ans, "usd": usd, "in": tin, "out": tout, "error": err, "secs": secs,
                          "posts": [p["id"] for p in b]}
            json.dump(state, open(a.state, "w"))
        print(model, k, "ok" if ans is not None else "FAIL " + str(err)[:120], f"USD {usd:.5f}", flush=True)

    with ThreadPoolExecutor(a.workers) as ex:
        for model in a.models.split(","):
            for k, b in enumerate(bs):
                ex.submit(one, model, k, b)


def report(a):
    posts = {str(p["id"]): p for p in (json.loads(l) for l in open(a.sample))}
    state = json.load(open(a.state))
    sts = [s["id"] for s in load_streams()]
    models = sorted({k.split("#")[0] for k in state})
    rows = []
    for m in models:
        runs = [v for k, v in state.items() if k.split("#")[0] == m]
        ok = [r for r in runs if r["answer"] is not None]
        need = kept = passed = pairs = missing = 0
        for r in ok:
            for pid in r["posts"]:
                pid = str(pid)
                p, got = posts[pid], r["answer"].get(pid)
                if got is None:
                    missing += 1
                    continue
                for sid in sts:
                    live = (p["scores"] or {}).get(sid) or {}
                    s = got.get(sid, 0)
                    pairs += 1
                    passed += s >= streams.SCREEN
                    if live.get("score") is not None and live["score"] >= 50 and live.get("model") != ai.MODEL:
                        need += 1
                        kept += s >= streams.SCREEN
        usd = sum(r["usd"] for r in ok)
        rows.append({"model": m, "calls": len(runs), "failed": len(runs) - len(ok), "posts_unanswered": missing,
                     "filed_pairs": need, "filed_pairs_kept": kept, "recall": round(kept / need, 4) if need else None,
                     "screened_in_share": round(passed / pairs, 4) if pairs else None,
                     "usd_per_call": round(usd / len(ok), 6) if ok else None,
                     "tokens_in": round(sum(r["in"] or 0 for r in ok) / max(len(ok), 1)),
                     "tokens_out": round(sum(r["out"] or 0 for r in ok) / max(len(ok), 1)),
                     "secs_per_call": round(sum(r["secs"] for r in ok) / max(len(ok), 1), 1),
                     "errors": sorted({(r["error"] or "")[:100] for r in runs if r["answer"] is None})[:3]})
    print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--sample", required=True)
    r.add_argument("--state", required=True)
    r.add_argument("--models", required=True)
    r.add_argument("--batches", type=int)
    r.add_argument("--workers", type=int, default=8)
    p = sub.add_parser("report")
    p.add_argument("--sample", required=True)
    p.add_argument("--state", required=True)
    a = ap.parse_args()
    run(a) if a.cmd == "run" else report(a)
