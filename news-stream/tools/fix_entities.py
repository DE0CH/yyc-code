#!/usr/bin/env python3
"""Repair the stored posts whose title, text, AI title or transcript holds HTML entities or HTML (corpus.plain), or
whose AI-written English title has Chinese quotation marks (titles.latin_quotes) (Deyao, 2026-10-21: "Apostrophe and
quotation mark for English sources are wrong"). Rows that are right are not touched: only a row whose repaired value
differs is written. The search columns are redone; the post is not made due again (scored.hash and
posts.ai_title_hash that matched the old content are set to the new one: no rescoring, no titles call, no second
alert). Stream items' copies (stream_items.item title/text) are repaired the same way (sent Telegram/WhatsApp messages
stay as sent).

Resumable: posts are walked in id order in chunks, the last finished id kept in kv fix_entities:last_id (--restart
starts again from 0); every change is logged as a JSON line (id, field, before, after) to --log.

    DATABASE_URL=… python3 tools/fix_entities.py [--dry-run] [--restart] [--chunk 50000] [--log FILE]
"""
import argparse, hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import corpus, streams, titles  # noqa: E402

KV = "fix_entities:last_id"
# what plain() could change: an entity ending in ";" or an HTML tag
SUSPECT = r"&(#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});|<(p|div|br|span|a|img|li|h[1-6])\M"
CJK_QUOTES = "[「」『』＂＇]"


def md5(title, text):
    return hashlib.md5(((title or "") + "\n" + (text or "")).encode()).hexdigest()


def repaired(r):
    """{field: new value} for the fields of post row r that are wrong"""
    out = {}
    for f in ("title", "text", "transcript"):
        v = corpus.plain(r[f])
        if v != r[f]:
            out[f] = v
    a = corpus.plain(r["ai_title"])
    if a and r["ai_title_kind"] in ("written", "retitled"):
        a = titles.latin_quotes(a)
    if a != r["ai_title"]:
        out["ai_title"] = a
    return out


def fix_post(c, r, new, log, dry):
    log.write(json.dumps({"id": r["id"], "sid": r["sid"], **{f: [r[f], v] for f, v in new.items()}},
                         ensure_ascii=False) + "\n")
    if dry:
        return
    v = {f: new.get(f, r[f]) for f in ("title", "text", "transcript", "ai_title")}
    with c:
        old_h = c.execute(f"SELECT {streams.HASH} AS h FROM posts p WHERE p.id=%s", (r["id"],)).fetchone()["h"]
        c.execute(f"UPDATE posts SET title=%s, text=%s, transcript=%s, ai_title=%s, {corpus.SEARCH_SET} WHERE id=%s",
                  (v["title"], v["text"], v["transcript"], v["ai_title"])
                  + corpus.search_cols(v["title"], v["text"], r["author"], r["source"], r["thread"], v["ai_title"],
                                       v["transcript"], r["image_text"]) + (r["id"],))
        new_h = c.execute(f"SELECT {streams.HASH} AS h FROM posts p WHERE p.id=%s", (r["id"],)).fetchone()["h"]
        c.execute("UPDATE scored SET hash=%s WHERE post_id=%s AND hash=%s", (new_h, r["id"], old_h))
        c.execute("UPDATE posts SET ai_title_hash=%s WHERE id=%s AND ai_title_hash=%s",
                  (md5(v["title"], v["text"]), r["id"], md5(r["title"], r["text"])))


def fix_items(c, log, dry):
    """stream_items' own copies of the title and text"""
    n = 0
    for r in c.execute(f"""SELECT id, item->>'title' AS title, item->>'text' AS text FROM stream_items
                           WHERE item->>'title' ~ %s OR item->>'text' ~ %s""", (SUSPECT, SUSPECT)).fetchall():
        t, x = corpus.plain(r["title"]), corpus.plain(r["text"])
        if (t, x) == (r["title"], r["text"]):
            continue
        n += 1
        log.write(json.dumps({"stream_item": r["id"], "title": [r["title"], t], "text": [r["text"], x]},
                             ensure_ascii=False) + "\n")
        if not dry:
            with c:
                c.execute("""UPDATE stream_items SET item = item || jsonb_strip_nulls(jsonb_build_object(
                               'title', %s::text, 'text', %s::text)) WHERE id=%s""", (t, x, r["id"]))
    return n


def run(dry=False, restart=False, chunk=50000, log_path=None):
    c = corpus.db()
    start = 0 if restart or dry else int(corpus.kv_get(KV) or 0)
    top = c.execute("SELECT max(id) AS m FROM posts").fetchone()["m"] or 0
    log = open(log_path or os.devnull, "a")
    n = 0
    lo = start
    while lo < top:
        hi = lo + chunk
        rows = c.execute(f"""SELECT id, sid, title, text, transcript, ai_title, ai_title_kind, author, source, thread,
                                    image_text FROM posts
                             WHERE id > %s AND id <= %s AND (title ~ %s OR text ~ %s OR transcript ~ %s OR ai_title ~ %s
                                   OR (ai_title ~ %s AND ai_title_kind IN ('written', 'retitled')))""",
                         (lo, hi, SUSPECT, SUSPECT, SUSPECT, SUSPECT, CJK_QUOTES)).fetchall()
        for r in rows:
            new = repaired(r)
            if new:
                n += 1
                fix_post(c, r, new, log, dry)
        log.flush()
        if not dry:
            corpus.kv_set(KV, hi)
        print(f"ids {lo + 1}–{hi}: {len(rows)} suspect, {n} {'to fix' if dry else 'fixed'} so far", flush=True)
        lo = hi
    m = fix_items(c, log, dry)
    log.close()
    print(f"{'would fix' if dry else 'fixed'} {n} post(s) and {m} stream item(s)")
    return n, m


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--chunk", type=int, default=50000)
    ap.add_argument("--log")
    a = ap.parse_args()
    run(a.dry_run, a.restart, a.chunk, a.log)
