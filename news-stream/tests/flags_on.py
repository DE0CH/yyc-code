"""For tests of the pipeline with picture analysis and transcription ON (they are off in the shipped flags.json since
2026-10-20, Deyao: cost; tests/test_pictures_transcription_off.py tests that): import this before flags.py is used.
It points FLAGS_FILE at a copy of flags.json with `pictures` and `transcription` on (the edge app reads them from
edge:config, which edge_config.py builds from the same flags)."""
import json, os, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src = os.environ.get("FLAGS_FILE") or os.path.join(HERE, "deploy", "apps", "flags", "flags.json")
_doc = json.load(open(_src))
for _n in ("pictures", "transcription"):
    _doc["flags"][_n]["defaultVariant"] = "on"
FILE = os.path.join(tempfile.mkdtemp(), "flags.json")
json.dump(_doc, open(FILE, "w"))
os.environ["FLAGS_FILE"] = FILE
os.environ.pop("FLAGD_HOST", None)
