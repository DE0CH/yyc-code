#!/usr/bin/env python3
"""Correct the stored posts that have no title of their own (corpus.from_body; Deyao, 2026-10-20: a Facebook post's
first paragraph shown as its title) seen from the restart cutoff (corpus.FLOOR) on: the full text kept as the
text, the title its opening (corpus.body_title), title_from_body = TRUE, the search columns redone. The post is not
made due again: scored.hash and posts.ai_title_hash that matched the old content are set to the new one (no
rescoring, no titles call). Its stream items' title becomes the excerpt (sent Telegram messages stay as sent).

    DATABASE_URL=… python3 tools/fix_body_titles.py [--dry-run]
"""
import hashlib, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import corpus  # noqa: E402


def md5(title, text):
    return hashlib.md5(((title or "") + "\n" + (text or "")).encode()).hexdigest()


def run(dry=False):
    c = corpus.db()
    rows = c.execute("""SELECT id, url, sid, platform, title, text, author, source, thread, ai_title, transcript, image_text
                        FROM posts WHERE thread IS NULL AND title_from_body IS NULL AND seen >= %s""",
                     (corpus.FLOOR,)).fetchall()
    n = 0
    for r in rows:
        if (r["title"] or "").startswith(corpus.LINK_TITLE) or not corpus.from_body(
                r["sid"], r["platform"], None, r["title"], r["text"]):
            continue
        title, text = corpus.body_title(r["title"], r["text"])
        n += 1
        if dry:
            print(r["id"], repr((r["title"] or "")[:60]), "→", repr(title))
            continue
        old, new = md5(r["title"], r["text"]), md5(title, text)
        with c:
            c.execute(f"UPDATE posts SET title=%s, text=%s, title_from_body=TRUE, {corpus.SEARCH_SET} WHERE id=%s",
                      (title, text) + corpus.search_cols(title, text, r["author"], r["source"], None, r["ai_title"],
                                                         r["transcript"], r["image_text"]) + (r["id"],))
            c.execute("UPDATE scored SET hash=%s WHERE post_id=%s AND hash=%s", (new, r["id"], old))
            c.execute("UPDATE posts SET ai_title_hash=%s WHERE id=%s AND ai_title_hash=%s", (new, r["id"], old))
            c.execute("UPDATE stream_items SET item = jsonb_set(item, '{title}', to_jsonb(%s::text)) WHERE url=%s",
                      (title, r["url"]))
    # their comment rows carry the post's title (corpus.add_comments): the excerpt now
    m = 0
    for r in c.execute("""SELECT k.id, k.title, k.text, k.author, k.source, k.thread, k.transcript, k.image_text, p.title AS ptitle
                          FROM posts k JOIN posts p ON p.url = k.thread AND p.title_from_body
                          WHERE k.thread IS NOT NULL AND k.seen >= %s AND k.title IS DISTINCT FROM p.title""",
                       (corpus.FLOOR,)).fetchall():
        m += 1
        if dry:
            continue
        old, new = md5(r["title"], r["text"]), md5(r["ptitle"], r["text"])
        with c:
            c.execute(f"UPDATE posts SET title=%s, {corpus.SEARCH_SET} WHERE id=%s",
                      (r["ptitle"],) + corpus.search_cols(r["ptitle"], r["text"], r["author"], r["source"], r["thread"],
                                                          None, r["transcript"], r["image_text"]) + (r["id"],))
            c.execute("UPDATE scored SET hash=%s WHERE post_id=%s AND hash=%s", (new, r["id"], old))
    print(f"{'would fix' if dry else 'fixed'} {n} post(s) and {m} comment row(s)")
    return n


if __name__ == "__main__":
    run("--dry-run" in sys.argv)
