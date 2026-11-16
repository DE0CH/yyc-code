#!/usr/bin/env python3
"""Clean the YouTube subtitles stored before the parser fix of 2026-10-20 (media.reclean_captions): media.captions
held WebVTT cue settings ("align:start position:0%") and each rolling-caption line two or three times, and Chinese
tracks labelled plain zh (or even zh-Hant) but written in Simplified stayed Simplified. Rewrites only
the text: media.captions, then the posts' transcript and search text through media.refresh_post (posts seen before
corpus.FLOOR are never scored again; later ones are scored on the clean text). A clip with nothing left
(the old parser lost its text) is read again while queued. Dry run unless --apply.

    DATABASE_URL=postgresql://… python3 infra/reclean-captions.py [--apply]
"""
import os, re, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import corpus, media  # noqa: E402


def run(apply=False):
    rows = corpus.db().execute("""SELECT key, status, captions, captions_model FROM media
                                  WHERE captions ~ 'align:[a-z]+ position:' OR captions_model ~ '^youtube-(auto-)?captions:(zh|yue)'""").fetchall()
    changed, before, after, again = 0, 0, 0, 0
    for r in rows:
        new = media.reclean_captions(r["captions"], r["captions_model"])
        if new == r["captions"]:
            continue
        changed += 1
        before += len(r["captions"])
        after += len(new or "")
        if not re.sub(r"\[[\d:]+\]", "", new or "").strip():
            # nothing left: the old parser lost the text (a cue's ' ' line ended it there); a queued clip's subtitles
            # are read again (captions_one), a transcribed clip's are dropped
            again += r["status"] == "queued"
            if apply:
                corpus.db().execute("""UPDATE media SET captions=NULL, captions_model=NULL,
                                       captions_at=CASE WHEN status='queued' THEN NULL ELSE captions_at END WHERE key=%s""",
                                    (r["key"],))
        elif apply:
            corpus.db().execute("UPDATE media SET captions=%s WHERE key=%s", (new, r["key"]))
        if apply:
            for p in corpus.db().execute("SELECT post_id FROM post_media WHERE key=%s", (r["key"],)).fetchall():
                media.refresh_post(p["post_id"])
    left = corpus.db().execute("SELECT count(*) AS n FROM media WHERE captions ~ 'align:[a-z]+ position:'").fetchone()["n"]
    print(f"{'cleaned' if apply else 'would clean'} {changed} of {len(rows)} clips' subtitles: {before} → {after} characters;"
          f" {again} queued clips with nothing left to read again; {left} still carry cue settings", flush=True)
    return changed


if __name__ == "__main__":
    run("--apply" in sys.argv[1:])
