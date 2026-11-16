#!/usr/bin/env python3
"""YouTube subtitles stored as a post's transcript are clean text (Deyao, 2026-10-20: stored subtitles carried WebVTT
cue settings such as "align:start position:0%" and every rolling-caption line two or three times, and that text went
to the scorer, to search and to the item page). No database, no network: real caption files YouTube served on
2026-10-20 (tests/fixtures/vtt/, fetched with yt-dlp through yycstream), and what the old parser stored from them.

    python3 tests/test_vtt_clean.py

1. media.vtt_lines: no cue settings, no timing lines, no inline timestamps or <c> tags, entities decoded; each line
   of a rolling caption (YouTube's auto-generated tracks; broadcasters' roll-up captions) once; '[mm:ss]' marks about
   once a minute (CHUNK seconds apart at least); Hong Kong Traditional (s2hk) as before.
2. A caption file with plain cues (a broadcaster's zh-HK track) keeps every cue's text, in order.
3. Any Chinese track written in Simplified characters (whatever its label: plain zh, even zh-Hant/zh-TW) goes through
   s2hk; Traditional and Cantonese text is left as it is.
4. media.reclean_captions turns what the old parser stored back into clean text (the stored rows are cleaned in place
   by infra/reclean-captions.py): the same text the fixed parser gives for that file (its marks where the old
   parser put them).
"""
import gzip, os, re, sys, unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
import media  # noqa: E402

FIX = os.path.join(HERE, "tests", "fixtures", "vtt")
FILES = {  # file → (track, auto-generated?)
    "9tJR5KOHS2M.en-orig": ("en-orig", True),          # YouTube auto captions, English
    "0cWbcEpn2eU.yue-orig": ("yue-orig", True),        # YouTube auto captions, Cantonese
    "bQ-mw1_OhSc.en-uYU-mmqFLq8": ("en-uYU-mmqFLq8", False),  # a TV station's roll-up captions (uploader's track)
    "fzn29zpQkfM.zh-HK": ("zh-HK", False),             # an uploader's plain zh-HK track
    "0TFKKMbkAf8.zh": ("zh", False),                   # an uploader's track labelled plain zh, written in Simplified
}
JUNK = re.compile(r"align:|position:\d|line:\d|-->|<\d\d:|</?c>|&gt;|&amp;|\d\d:\d\d:\d\d\.\d{3}")
MARK = re.compile(r"^\[(?:(\d+):)?(\d\d):(\d\d)\] ")


def vtt(name):
    return gzip.open(os.path.join(FIX, name + ".vtt.gz"), "rt", encoding="utf-8").read()


def stored(name):
    return gzip.open(os.path.join(FIX, name + ".stored.txt.gz"), "rt", encoding="utf-8").read()


def parsed(name):
    lang, auto = FILES[name]
    return media.vtt_lines(vtt(name), lang, auto)


def body(text):
    """the transcript without its marks, as one string"""
    return "".join(MARK.sub("", x) for x in text.splitlines())


def repeats(text, n=22):
    """runs of n characters (spaces aside; a caption line's length) that occur again within the next 120: a rolling
    caption shown twice (what was said twice is shorter: "be expecting more" twice in 9tJR5KOHS2M)"""
    t = re.sub(r"\s+", "", body(text))
    return [t[i:i + n] for i in range(len(t) - n) if t[i:i + n] in t[i + n:i + n + 120]]


class Parser(unittest.TestCase):
    def test_no_webvtt_markup(self):
        for name in FILES:
            out = parsed(name)
            self.assertTrue(out, name)
            self.assertIsNone(JUNK.search(out), f"{name}: {JUNK.search(out) and out[JUNK.search(out).start() - 40:][:120]}")

    def test_rolling_lines_once(self):
        for name in FILES:
            self.assertEqual(repeats(parsed(name)), [], name)

    def test_english_auto_captions(self):
        out = parsed("9tJR5KOHS2M.en-orig")
        self.assertTrue(out.startswith("[00:00] A super El Niño is used to informally refer to a rare or an exceptionally "
                                       "strong El Niño event, which is the warm phase of the El Niño Southern "
                                       "Oscillation. Uh this is a climate"), out[:300])
        self.assertEqual(out.count("A super El Niño is used to informally"), 1)

    def test_roll_up_captions(self):
        out = parsed("bQ-mw1_OhSc.en-uYU-mmqFLq8")
        self.assertTrue(out.startswith("[00:07] >> THE POPULAR 200 FOOT TALL X 2 ROLLER COASTER AT 6 FLAGS MAGIC "
                                       "MOUNTAIN HAS PERMANENTLY SHUT DOWN DUE TO CLAIMS OF"), out[:300])
        self.assertEqual(out.count("MAGIC MOUNTAIN HAS PERMANENTLY"), 1)

    def test_cantonese_auto_captions(self):
        out = parsed("0cWbcEpn2eU.yue-orig")
        self.assertTrue(out.startswith("[00:00] 一講起亞洲嘅神話同怪獸，你會諗起啲咩呢？我就諗起上奔魚同埋西遊記啦，今日等我帶大家嚟到"
                                       "西九文化區"), out[:200])
        self.assertEqual(out.count("一講起亞洲嘅神話同怪獸"), 1)

    def test_plain_cues_keep_everything(self):
        name = "fzn29zpQkfM.zh-HK"
        cues = [b.split("\n", 1)[1] for b in re.split(r"\n\s*\n", vtt(name)) if "-->" in b and "\n" in b]
        want = "".join(x.strip() for c in cues for x in c.split("\n") if x.strip())
        self.assertEqual(re.sub(r"\s+", "", body(parsed(name))), re.sub(r"\s+", "", media.to_hk(want)))

    def test_marks_about_once_a_minute(self):
        for name in FILES:
            secs = []
            for line in parsed(name).splitlines():
                m = MARK.match(line)
                self.assertTrue(m, f"{name}: {line[:60]}")
                secs.append(int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3)))
            self.assertGreater(len(secs), 1, name)
            self.assertTrue(all(b - a >= media.CHUNK for a, b in zip(secs, secs[1:])), f"{name}: {secs}")

    def test_hk_traditional(self):
        out = media.vtt_lines("WEBVTT\n\n00:00:01.000 --> 00:00:04.000 align:start position:0%\n大家好，欢迎收睇\n", "zh-Hans")
        self.assertEqual(out, "[00:01] 大家好，歡迎收睇")


class Simplified(unittest.TestCase):
    def test_plain_zh_track_in_simplified(self):
        out = parsed("0TFKKMbkAf8.zh")
        self.assertTrue(out.startswith("[00:00] 您將看到……親愛的朋友們，大家好🌿❤️ 我們很高興再次與大家見面。"), out[:120])
        self.assertTrue(media.simplified(stored("0TFKKMbkAf8.zh")))
        self.assertFalse(media.simplified(out))

    def test_traditional_and_cantonese_untouched(self):
        for name in ("fzn29zpQkfM.zh-HK", "0cWbcEpn2eU.yue-orig"):
            self.assertFalse(media.simplified(parsed(name)), name)
        trad = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n皇后大道中嘅天后廟，佢哋話我哋去食飯\n"
        self.assertEqual(media.vtt_lines(trad, "zh"), "[00:01] 皇后大道中嘅天后廟，佢哋話我哋去食飯")

    def test_labelled_traditional_but_simplified(self):
        out = media.vtt_lines("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n我叫赵凡在一次意外中觉醒契约系统\n", "zh-Hant")
        self.assertEqual(out, "[00:01] 我叫趙凡在一次意外中覺醒契約系統")


class Reclean(unittest.TestCase):
    def test_stored_rows_become_the_fixed_parsers_text(self):
        for name in FILES:
            lang, auto = FILES[name]
            got = media.reclean_captions(stored(name), (media.AUTO_CAPTIONS if auto else media.CAPTIONS) + media.caption_label(lang))
            self.assertIsNone(JUNK.search(got), name)
            self.assertEqual(re.sub(r"\s+", "", body(got)), re.sub(r"\s+", "", body(parsed(name))), name)
            self.assertTrue(all(MARK.match(x) for x in got.splitlines()), name)

    def test_settings_glued_to_chinese(self):
        """a stored row (yt:wubHIP3yv_k): the settings the old parser kept sit right against the text"""
        old = ("[41:02] align:start position:0%你買定嚟等，咁就誒係希望佢可以升173 align:start position:0%你買定嚟等，咁就誒係"
               "希望佢可以升173,如果你嗰陣時你冇講咧就係10個align:start position:0%,如果你嗰陣時你冇講咧就係10個align:start "
               "position:0%,如果你嗰陣時你冇講咧就係10個percent計")
        self.assertEqual(media.reclean_captions(old), "[41:02] 你買定嚟等，咁就誒係希望佢可以升173,如果你嗰陣時你冇講咧就係10個percent計")

    def test_stored_simplified_converted(self):
        self.assertEqual(media.reclean_captions(stored("0TFKKMbkAf8.zh"), "youtube-captions:zh"), parsed("0TFKKMbkAf8.zh"))
        self.assertEqual(media.reclean_captions("[00:00] 您将看到", "youtube-captions:en"), "[00:00] 您将看到")

    def test_clean_text_unchanged(self):
        for name in FILES:
            lang, auto = FILES[name]
            model = (media.AUTO_CAPTIONS if auto else media.CAPTIONS) + media.caption_label(lang)
            self.assertEqual(media.reclean_captions(parsed(name), model), parsed(name), name)


if __name__ == "__main__":
    unittest.main()
