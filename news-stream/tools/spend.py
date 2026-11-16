"""Paid tools' spend ledger: every paid call is recorded in tool_spend (who, which tool, the provider's own cost when it
reports one, else the listed price). Monitoring only: nothing is refused for having spent (Deyao, 2026-10-14: budgets
are safety stops; the provider refusing for its balance is the stop). infra/control.py tool-spend shows each account's
total. A paid call needs a signed-in account so its cost is attributed."""
import json

import corpus

SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_spend (
  id BIGSERIAL PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now(), email TEXT NOT NULL, tool TEXT NOT NULL,
  usd DOUBLE PRECISION NOT NULL, args JSONB, provider_cost DOUBLE PRECISION, ok BOOLEAN NOT NULL DEFAULT TRUE);
CREATE INDEX IF NOT EXISTS tool_spend_email ON tool_spend (email, at);
DROP TABLE IF EXISTS tool_budget;
"""
_ready = False


def _db():
    global _ready
    db = corpus.db()
    if not _ready:
        for stmt in [s for s in SCHEMA.split(";") if s.strip()]:
            db.execute(stmt)
        _ready = True
    return db


def everyone():
    """infra/control.py tool-spend: {email, usd, calls, last} per account that ran a paid tool"""
    rows = _db().execute("""SELECT email, coalesce(sum(usd), 0), count(*), max(at) FROM tool_spend
                            GROUP BY email ORDER BY email""").fetchall()
    return [{"email": r[0], "usd": round(r[1], 4), "calls": r[2], "last": str(r[3])} for r in rows]


def price(t, args):
    p = t.price
    return float(p["usd"](args) if callable(p.get("usd")) else p["usd"])


def charged(t, args, ctx):
    """run a paid tool for a signed-in account and record what it cost"""
    from . import fit
    email = ctx.get("email") or ""
    if not email:
        raise ValueError(f"{t.name} costs money and needs a signed-in account; this caller has none")
    usd = price(t, args)
    ok, provider_cost = False, None
    try:
        out = t.fn(args, ctx)
        ok = True
        if isinstance(out, dict) and isinstance(out.get("_cost_usd"), (int, float)):
            provider_cost = float(out.pop("_cost_usd"))
        return fit(dict(out, charged_usd=provider_cost if provider_cost is not None else usd)) if isinstance(out, dict) else out
    finally:
        _db().execute("INSERT INTO tool_spend (email, tool, usd, args, provider_cost, ok) VALUES (%s,%s,%s,%s,%s,%s)",
                      (email, t.name, provider_cost if provider_cost is not None else (usd if ok else 0.0),
                       json.dumps(args, ensure_ascii=False), provider_cost, ok))
