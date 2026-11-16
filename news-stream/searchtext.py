"""Chinese-first search text (Deyao, 2026-10-14): the hidden search copy of a post and of every query term, so a search
in Traditional finds Simplified posts and the reverse, words are matched as words, and display text is never changed.

    norm(s)        NFKC (full-width → half-width) → OpenCC hk2s (Hong Kong Traditional → Simplified, so the HK variants
                   裏/裡, 綫/線, 衞/衛 merge too) → lower case. posts.hay, posts.stitle and every query term go through it.
    tokens(s)      jieba (accurate mode, HMM off, so an unknown name splits the same way in a query and in a post) over
                   norm()ed text, with jieba's own dictionary plus search_dict.txt (Hong Kong officials, places,
                   organisations, Cantonese written words, the streams' entities) and the streams' Chinese one-word
                   search queries (streams.json); tokens without a letter or digit (punctuation, spaces) are dropped.
                   English words are kept as they are, lower case, not stemmed: the substring fallback already finds
                   "elections" for "election", the query language is exact-term (K-Matrix's), and the Cloudflare
                   Worker can then segment queries the same way without an English stemmer.
    tsvector(…)    posts.words: the tokens as a tsvector literal (lexeme:position, the title's tokens weight A), built
                   here and cast with ::tsvector, so no Postgres text-search parser, dictionary or extension is used
                   (YYC's Postgres, the CloudNativePG image: no zhparser, pg_jieba or pg_bigm). A token of 3+ characters
                   also indexes the dictionary words inside it at its position (jieba's search mode: 新資本投資者入境計劃
                   is found by 入境).
    tsquery(term)  a query term's tokens as a phrase tsquery ('a' <-> 'b'), weight A only for titles-only searches.
    VERSION        a hash of the dictionaries and this code's rules; posts.search_v holds the version a row was indexed
                   with, and corpus.reindex() redoes the rows that differ (so editing search_dict.txt reindexes history).

query.py: a term matches a post by word (posts.words @@ tsquery(term)) or, as the fallback for names and new terms the
dictionary misses, by substring of the normalised text (strpos(posts.hay, norm(term)), narrowed by the posts.grams
character-pair index); word matches are ranked before fallback-only matches.
"""
import hashlib, json, logging, os, re, threading, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
DICT = os.path.join(HERE, "search_dict.txt")
STREAMS = os.path.join(HERE, "streams.json")
RULES = "1"  # bump when norm/tokens/tsvector change in a way that needs every row redone
MAX_LEXEME = 60   # characters; a longer token (a run of letters with no break) is not indexed as a word
MAX_POS = 16383   # Postgres' highest tsvector position
MAX_POSITIONS = 255  # positions kept per lexeme
SUB_LEN = 32  # characters kept of each suffix in post_subs (corpus.SCHEMA's subs_of): a Latin term up to this long is looked up there
_SUB_TERM = re.compile(r"[a-z0-9]{1,%d}" % SUB_LEN)

_lock = threading.Lock()
_cc = {}
_tok = None


def sub_term(n):
    """a normalised term that post_subs answers (only a-z/0-9, at most SUB_LEN long), else None. post_subs.subs holds
    every suffix, cut to SUB_LEN characters, of each run of a-z/0-9 in a post's hay (corpus.SCHEMA subs_of): such a
    term is a substring of hay exactly when one of them starts with it (the occurrence lies inside one run, and that
    run's suffix from the occurrence starts with the term), so it is the GIN prefix lookup 'term':*"""
    return n if n and _SUB_TERM.fullmatch(n) else None


def _opencc(cfg):
    c = _cc.get(cfg)
    if c is None:
        import opencc
        c = _cc[cfg] = opencc.OpenCC(cfg)
    return c


def norm(s):
    """the search form of a text: half-width, Simplified, lower case"""
    if not s:
        return ""
    return _opencc("hk2s").convert(unicodedata.normalize("NFKC", s)).lower()


def variants(term):
    """the forms a term may take in the displayed (original) text, for highlighting and extracts: as typed, its
    Simplified form, and that in HK and standard Traditional"""
    n = norm(term)
    out = [term.lower(), n, _opencc("s2hk").convert(n), _opencc("s2t").convert(n)]
    return [x for x in dict.fromkeys(out) if x]


def _dict_words():
    """(word, freq or None) from search_dict.txt (Traditional, as people write it; normalised here) and the streams'
    Chinese one-word search queries (streams.json news_queries / discuss_queries / social_queries)"""
    out = []
    with open(DICT, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            freq = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            out.append((parts[0], freq))
    try:
        with open(STREAMS, encoding="utf-8") as f:
            streams = json.load(f)
        for s in (streams if isinstance(streams, list) else streams.get("streams", [])):
            for k in [q for key in ("news_queries", "discuss_queries", "social_queries") for q in s.get(key) or []]:
                if k and not any(c.isspace() for c in k) and any(ord(c) > 0x2e7f for c in k):
                    out.append((k, None))
    except (OSError, ValueError):
        pass
    return out


def _version():
    h = hashlib.sha256(RULES.encode())
    for w, f in sorted(set(_dict_words()), key=lambda x: (x[0], x[1] or 0)):
        h.update(f"{w}\t{f}\n".encode())
    return h.hexdigest()[:12]


VERSION = _version()


def tokenizer():
    """the jieba Tokenizer with our dictionary (loaded once per process, about 1 s and 80 MB)"""
    global _tok
    if _tok is None:
        with _lock:
            if _tok is None:
                import jieba
                jieba.setLogLevel(logging.WARNING)
                t = jieba.Tokenizer()
                if os.environ.get("JIEBA_CACHE_DIR"):  # the image's prebuilt model cache (Dockerfile)
                    t.tmp_dir = os.environ["JIEBA_CACHE_DIR"]
                t.initialize()
                for w, f in _dict_words():
                    n = norm(w)
                    if n and not any(c.isspace() for c in n):
                        t.add_word(n, f)
                _tok = t
    return _tok


def _keep(w):
    return len(w) <= MAX_LEXEME and any(c.isalnum() for c in w)


def tokens(s):
    """the words of an already norm()ed text, in order"""
    if not s:
        return []
    return [w for w in tokenizer().cut(s, HMM=False) if _keep(w.strip())]


def _sub(w, freq):
    """dictionary words inside a token (jieba's search mode: every length from 2 to len-1)"""
    if len(w) < 3:
        return []
    return [w[i:i + n] for n in range(2, len(w)) for i in range(len(w) - n + 1) if freq.get(w[i:i + n])]


def _lex(w):
    return "'" + w.replace("\\", "\\\\").replace("'", "''") + "'"


def tsvector(title, body):
    """posts.words as a tsvector literal: the tokens of title (weight A) then body (norm()ed texts); '' when empty"""
    freq = tokenizer().FREQ
    pos = {}
    weight = {}
    p = 0
    for part, wt in ((title, "A"), (body, "")):
        for w in tokens(part):
            w = w.strip()
            p = min(p + 1, MAX_POS)
            for x in [w] + _sub(w, freq):
                ps = pos.setdefault(x, [])
                if len(ps) < MAX_POSITIONS and (not ps or ps[-1] != p):
                    ps.append(p)
                    if wt:
                        weight[(x, p)] = wt
    return " ".join(_lex(w) + ":" + ",".join(f"{q}{weight.get((w, q), '')}" for q in ps) for w, ps in pos.items())


def tsquery(term, title_only=False):
    """a norm()ed query term as a phrase tsquery literal; None when it has no word (only punctuation)"""
    ws = [w.strip() for w in tokens(term)]
    if not ws:
        return None
    return " <-> ".join(_lex(w) + (":A" if title_only else "") for w in ws)


# ---- what the edge app (web-app/, the Cloudflare Worker) needs to segment and normalise QUERY terms as tokens() and
# norm() do here (web-app/src/server/search/). The posts' columns stay written here; the Worker only cuts the query.
#   table search_words (v, w, lp)   every word of the tokenizer's dictionary that a query can contain (frequency > 0, only
#                                   characters jieba cuts as one block) with lp = math.log(frequency), so the Worker's
#                                   route sums are bit-for-bit this process's; v = EDGE stamp. Read per query for the
#                                   candidate substrings of its terms (one indexed lookup), never loaded whole.
#   kv search:opencc                the OpenCC configs norm() and variants() use (hk2s, s2hk, s2t) and every dictionary
#                                   they name, dumped from this process's own OpenCC package (opencc_dict), keyed by hash
#   kv search:edge                  {stamp, version, logtotal, maxlen, opencc (hash), jieba, opencc_version, at}
# publish_edge() (edge_config.publish, every minute) rewrites them when the stamp changes: a search_dict.txt edit, a
# streams.json query, jieba or OpenCC upgraded. Old stamps' rows go after the new kv is written (one previous kept).
EDGE_CONFIGS = ("hk2s", "s2hk", "s2t")
EDGE_RULES = "1"  # bump when what publish_edge() writes changes shape


_dump = None


def _opencc_dump():
    """{"configs": {name: config json}, "dicts": {file: "key\\tvalue …\\n…"}} from the installed OpenCC package (once per
    process: the package can't change under it)"""
    global _dump
    if _dump is not None:
        return _dump
    import subprocess, tempfile, opencc
    share = os.path.join(os.path.dirname(opencc.__file__), "clib", "share", "opencc")
    tool = os.path.join(os.path.dirname(opencc.__file__), "clib", "bin", "opencc_dict")
    configs, files = {}, []

    def walk(d):
        if isinstance(d, dict):
            if d.get("type") == "ocd2" and d.get("file") not in files:
                files.append(d["file"])
            for v in d.values():
                walk(v)
        elif isinstance(d, list):
            for v in d:
                walk(v)

    for name in EDGE_CONFIGS:
        with open(os.path.join(share, name + ".json"), encoding="utf-8") as f:
            configs[name] = json.load(f)
        walk(configs[name])
    dicts = {}
    with tempfile.TemporaryDirectory() as tmp:
        for fn in files:
            out = os.path.join(tmp, fn + ".txt")
            subprocess.run([tool, "-i", os.path.join(share, fn), "-o", out, "-f", "ocd2", "-t", "text"], check=True,
                           capture_output=True)
            with open(out, encoding="utf-8") as f:
                dicts[fn] = f.read()
    _dump = {"configs": configs, "dicts": dicts}
    return _dump


def edge_words():
    """(word, math.log(freq)) for every dictionary word a query block can contain, and log(total)"""
    import math, re
    t = tokenizer()
    block = re.compile("[一-鿕a-zA-Z0-9+#&\\._%\\-]+")  # jieba's re_han_default: only these are cut by the DAG
    words = [(w, math.log(f)) for w, f in t.FREQ.items() if f and block.fullmatch(w)]
    return words, math.log(t.total)


def _ranges(pred):
    out, start = [], None
    for c in range(0x110001):
        ok = c < 0x110000 and pred(chr(c))
        if ok and start is None:
            start = c
        elif not ok and start is not None:
            out.append([start, c - 1])
            start = None
    return out


def _unicode_tables():
    """this Python's Unicode data where the Worker's JavaScript engine may be newer: the code points assigned here (the
    Worker leaves others unchanged by lower case and NFKC, as Python does) and those str.isalnum() accepts (tokens())"""
    return {"version": unicodedata.unidata_version, "assigned": _ranges(lambda ch: unicodedata.category(ch) != "Cn"),
            "alnum": _ranges(str.isalnum)}


def edge_stamp(opencc_hash):
    import jieba
    return hashlib.sha256(f"{VERSION}|{jieba.__version__}|{opencc_hash}|{unicodedata.unidata_version}|{EDGE_RULES}".encode()).hexdigest()[:12]


def publish_edge(force=False):
    """write search_words / search:opencc / search:edge for the current dictionaries when they changed → the stamp"""
    import corpus, opencc
    from psycopg.types.json import Jsonb
    dump = _opencc_dump()
    oc_hash = hashlib.sha256(json.dumps(dump, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    stamp = edge_stamp(oc_hash)
    cur = corpus.kv_get("search:edge") or {}
    if cur.get("stamp") == stamp and not force:
        return stamp
    words, logtotal = edge_words()
    def write():  # run again from the start when the database goes away under it (corpus.retrying)
        with corpus.pool().connection() as c:
            with c.transaction():
                c.execute("DELETE FROM search_words WHERE v = %s", (stamp,))
                with c.cursor().copy("COPY search_words (v, w, lp) FROM STDIN") as cp:
                    # the words hold only jieba's block characters: no tab, newline or backslash to escape
                    for i in range(0, len(words), 20000):
                        cp.write("".join(f"{stamp}\t{w}\t{lp!r}\n" for w, lp in words[i:i + 20000]))
                if (corpus.kv_get("search:opencc") or {}).get("hash") != oc_hash:
                    c.execute("INSERT INTO kv (k, v) VALUES ('search:opencc', %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
                              (Jsonb(dict(dump, hash=oc_hash)),))
                import jieba
                doc = {"stamp": stamp, "version": VERSION, "unicode": _unicode_tables(), "logtotal": logtotal, "maxlen": max(len(w) for w, _ in words),
                       "opencc": oc_hash, "jieba": jieba.__version__, "opencc_version": getattr(opencc, "__version__", None),
                       "prev": cur.get("stamp")}
                c.execute("INSERT INTO kv (k, v) VALUES ('search:edge', %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
                          (Jsonb(doc),))
    corpus.retrying(write, "search words")
    corpus.db().execute("DELETE FROM search_words WHERE v <> ALL(%s)", ([x for x in (stamp, cur.get("stamp")) if x],))
    return stamp
