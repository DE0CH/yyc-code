"""Keyword profile language (K-Matrix's operators), compiled to a Postgres WHERE clause over corpus.posts.

    滙豐 HSBC "hong kong bank"              whitespace = OR (as in K-Matrix's profiles: a list of alternatives)
    港投 AND (半導體 OR 晶片)                 AND, OR, NOT (upper case), ( ) to group; NOT binds tightest, then AND, then OR
    HSBC NOT (出售 "[售]")                    "…" = an exact phrase; #tag works as a plain term
    陳茂波 NEARBY/20 預算                      Nearby AND: both within 20 characters of each other (NEARBY alone = 10)
    FindIn(title: 施政報告)                    match only in a field: title, content, author, source
    IncData(Forum, LIHKG 時事台)               only these data sources: a source type (News, Forum, Social Media, Video,
    ExcData(tikhub_x, News)                   Blog, Official, Broadcast), a website name, a source id or a platform; ExcData leaves them out
    TopicSearchOnly                           match titles / thread topics only, not the text of posts

Matching (Deyao, 2026-10-14; searchtext.py): every term is normalised as the posts are (half-width, OpenCC hk2s →
Simplified, lower case), so Traditional and Simplified find each other. A term matches a post by its words (jieba;
posts.words @@ the term's words as a phrase) or, as the fallback for names and new terms the dictionary cuts
differently, as a substring of the normalised search text (strpos() on posts.hay; NEARBY = the SQL function near()
that corpus.SCHEMA creates, on the same text). rank_sql() puts word matches before substring-only matches. The
term as typed is tried as a substring too, for rows indexed before the change until corpus.reindex() reaches them.
A term of only a-z/0-9 (trump, housing, 2026) is a substring of a post exactly when post_subs.subs (the suffixes of
the text's Latin runs; corpus.SCHEMA subs_of) has a lexeme starting with it: its substring matches come from that
table's GIN prefix lookup, with no post text read (Deyao, 2026-10-27); a post without a post_subs row, or whose subs is
NULL (a text too long for a tsvector), keeps the strpos test.
"""
import re

import searchtext

FIELDS = ("title", "content", "author", "source")
SOURCE_TYPES = ("News", "Newspaper", "Magazine", "Forum", "Social Media", "Video", "Blog", "Official", "Broadcast")


class QueryError(ValueError):
    pass


TOKEN = re.compile(r'\s*(?:(?P<lp>\()|(?P<rp>\))|(?P<comma>,)|(?P<colon>:)|"(?P<phrase>[^"]*)"|(?P<word>[^\s(),:"]+))')


def tokenize(s):
    out, pos = [], 0
    s = s.replace("“", '"').replace("”", '"').replace("（", "(").replace("）", ")").replace("，", ",").replace("：", ":")
    while pos < len(s):
        if s[pos:].strip() == "":
            break
        m = TOKEN.match(s, pos)
        if not m or m.end() == pos:
            raise QueryError(f"cannot read the query at: {s[pos:pos + 20]}")
        pos = m.end()
        k = m.lastgroup
        out.append((k, m.group(k)))
    return out


class Parser:
    def __init__(self, s):
        self.t = tokenize(s)
        self.i = 0
        self.topic_only = False
        self.inc, self.exc = [], []

    def peek(self, k=None, v=None):
        if self.i >= len(self.t):
            return False
        tk, tv = self.t[self.i]
        return (k is None or tk == k) and (v is None or tv == v)

    def take(self):
        self.i += 1
        return self.t[self.i - 1]

    def word_is(self, *names):
        return self.peek("word") and self.t[self.i][1].split("/")[0].upper() in names

    def parse(self):
        node = self.or_() if self.i < len(self.t) else None
        if self.i < len(self.t):
            raise QueryError(f"unexpected {self.t[self.i][1]!r}")
        return node

    def starts_operand(self):
        if self.i >= len(self.t):
            return False
        k, v = self.t[self.i]
        if k in ("lp", "phrase"):
            return True
        return k == "word" and v.split("/")[0].upper() not in ("AND", "OR", "NEARBY", "NEAR")

    def or_(self):
        """a run of alternatives; a NOT after them excludes everything that follows in this group:
        A B NOT C D = (A OR B) AND NOT (C OR D)"""
        parts = []
        while True:
            if self.word_is("OR"):
                self.take()
            elif self.word_is("NOT") and [p for p in parts if p is not None]:
                self.take()
                rest = self.or_()
                left = self.combine(parts)
                return ("and", [left, ("not", rest)]) if rest is not None else left
            elif self.starts_operand():
                parts.append(self.and_())
            else:
                break
        return self.combine(parts)

    @staticmethod
    def combine(parts):
        parts = [p for p in parts if p is not None]
        return parts[0] if len(parts) == 1 else ("or", parts) if parts else None

    def and_(self):
        parts = [self.near()]
        while self.word_is("AND"):
            self.take()
            parts.append(self.near())
        parts = [p for p in parts if p is not None]
        return parts[0] if len(parts) == 1 else ("and", parts) if parts else None

    def near(self):
        left = self.not_()
        while self.word_is("NEARBY", "NEAR"):
            w = self.take()[1]
            n = int(w.split("/")[1]) if "/" in w and w.split("/")[1].isdigit() else 10
            right = self.not_()
            if not (left and left[0] == "term" and right and right[0] == "term"):
                raise QueryError("NEARBY joins two words or phrases")
            left = ("near", left[1], right[1], n)
        return left

    def not_(self):
        if self.word_is("NOT"):
            self.take()
            return ("not", self.not_())
        return self.atom()

    def args(self):
        """( a, b c, "d" ) → ['a', 'b c', 'd']"""
        if not self.peek("lp"):
            raise QueryError("expected ( after IncData/ExcData")
        self.take()
        out, cur = [], []
        while not self.peek("rp"):
            if self.i >= len(self.t):
                raise QueryError("missing )")
            k, v = self.take()
            if k == "comma":
                out.append(" ".join(cur))
                cur = []
            else:
                cur.append(v)
        self.take()
        if cur:
            out.append(" ".join(cur))
        return [a.strip() for a in out if a.strip()]

    def atom(self):
        if self.i >= len(self.t):
            raise QueryError("the query ends too early")
        k, v = self.t[self.i]
        if k == "lp":
            self.take()
            node = self.or_()
            if not self.peek("rp"):
                raise QueryError("missing )")
            self.take()
            return node
        if k == "phrase":
            self.take()
            return ("term", v.lower())
        if k == "word":
            u = v.upper()
            if u == "TOPICSEARCHONLY":
                self.take()
                self.topic_only = True
                return None
            if u in ("INCDATA", "EXCDATA") and self.i + 1 < len(self.t) and self.t[self.i + 1][0] == "lp":
                self.take()
                (self.inc if u == "INCDATA" else self.exc).extend(self.args())
                return None
            if u == "FINDIN" and self.i + 1 < len(self.t) and self.t[self.i + 1][0] == "lp":
                self.take()
                self.take()
                if not self.peek("word"):
                    raise QueryError("FindIn(field: words)")
                field = self.take()[1].lower()
                if field not in FIELDS:
                    raise QueryError(f"FindIn field must be one of {', '.join(FIELDS)}")
                if not self.peek("colon"):
                    raise QueryError("FindIn(field: words) needs a colon")
                self.take()
                node = self.or_()
                if not self.peek("rp"):
                    raise QueryError("missing ) after FindIn")
                self.take()
                return ("field", field, node)
            self.take()
            return ("term", v.lower())
        raise QueryError(f"unexpected {v!r}")


def parse(s):
    p = Parser(s or "")
    node = p.parse()
    return {"node": node, "topic_only": p.topic_only, "inc": p.inc, "exc": p.exc}


def terms(node):
    """every positive search term (for highlighting and hot-term exclusion)"""
    if node is None:
        return []
    k = node[0]
    if k == "term":
        return [node[1]]
    if k == "near":
        return [node[1], node[2]]
    if k == "not":
        return []
    if k == "field":
        return terms(node[2])
    return [t for n in node[1] for t in terms(n)]


def _tsq(g):
    """one character pair as a tsquery lexeme (quoted: any character may be in it)"""
    return "'" + g.replace("\\", "\\\\").replace("'", "''") + "'"


def _pairs(t):
    return sorted({t[i:i + 2] for i in range(len(t) - 1)})


def variants(terms_):
    """every form the terms may take in the displayed text (as typed, Simplified, HK and standard Traditional), for
    highlighting and extracts"""
    return list(dict.fromkeys(v for t in terms_ for v in searchtext.variants(t)))


# Where a term is looked for (a "context"): "all" = the post's search text (posts.hay + posts.words), "title" = the
# title only (TopicSearchOnly, FindIn(title:)), or a raw column (FindIn content/author/source), which has no
# normalised copy: there the term's written forms (searchtext.variants) are each tried as substrings.
RAW = {"content": "lower(coalesce(p.text,''))", "author": "lower(coalesce(p.author,''))",
       "source": "lower(p.source || ' ' || p.website)"}
TITLE_COL = "coalesce(p.stitle, lower(p.title))"  # stitle is NULL on a row not reindexed yet


def _forms(t, ctx):
    """the substrings that count as the term in this context: its normalised form, plus the form as typed (a row not
    reindexed yet still holds the old, unconverted hay), or every written form in a raw column"""
    if ctx in RAW:
        return searchtext.variants(t)
    return list(dict.fromkeys([searchtext.norm(t), t]))


SUBS_KEY = "search:subs"  # kv: true once corpus.fill_subs() has written post_subs for every post
_ready = {"at": 0.0, "v": False}


# the posts whose subs hold a lexeme starting with the term, or have no list (subs NULL): an id list (one GIN lookup on
# post_subs, an InitPlan), so it joins posts' other index lookups in one bitmap (posts_pkey)
SUBS_IDS = "p.id = ANY(ARRAY(SELECT s.post_id FROM post_subs s WHERE s.subs @@ %s::tsquery OR s.subs IS NULL))"
# the exact test of one post: its subs' prefix lookup, or strpos when it has no list (no row, or subs NULL)
SUBS_TEST = "coalesce((SELECT s.subs @@ %s::tsquery FROM post_subs s WHERE s.post_id = p.id), ({strpos}))"


def subs_ready():
    """whether every post has its post_subs row (kv search:subs, re-read every 30 s; the triggers write the new ones).
    Before: a Latin term's index lookup is its words and grams, as for any term; after: its words and the post_subs
    prefix lookup (plus the posts whose subs is NULL)"""
    import time
    if time.monotonic() - _ready["at"] > 30:
        import corpus
        _ready.update(at=time.monotonic(), v=corpus.kv_get(SUBS_KEY) is True)
    return _ready["v"]


def _sub(t, ctx):
    """the term's prefix tsquery on post_subs (searchtext.sub_term) when it is one (only a-z/0-9, at most SUB_LEN
    characters once normalised) and is looked for in the search text, else None. A post's subs come from its hay in today's
    search form, which holds no character that norm() changes (upper case, full-width…), so the term's other form (as
    typed) can't be in it and the prefix lookup alone decides"""
    n = searchtext.sub_term(searchtext.norm(t)) if ctx == "all" else None
    return _tsq(n) + ":*" if n else None


def _word(t, ctx):
    """the term's word tsquery in this context (None: no word lookup there)"""
    if ctx in RAW:
        return None
    return searchtext.tsquery(searchtext.norm(t), title_only=ctx == "title")


def prefilter(q):
    """the parsed query as an index lookup → (sql, params) or None when no index can narrow it: a term's words
    (posts.words @@ its phrase) or all the character pairs of one of its forms (posts.grams). Terms with a one-character
    form are looked up by prefix (corpus.grams). NOT (anything may match), FindIn title/source and TopicSearchOnly (a
    comment row's hay has neither its thread's title nor its source) and an OR with any such branch give None. Only
    narrows: to_sql's tests decide."""
    if q["topic_only"]:
        return None

    def t(node, ctx="all"):
        k = node[0]
        if k == "term":
            forms = [f for f in dict.fromkeys(searchtext.variants(node[1]) + _forms(node[1], ctx)) if f]
            if not forms:
                return None
            g = " | ".join("(" + " & ".join(_tsq(x) for x in _pairs(f)) + ")" if len(f) > 1 else _tsq(f) + ":*"
                           for f in forms)
            w = _word(node[1], "all")
            n = _sub(node[1], ctx)
            if n and subs_ready():  # post_subs answers it exactly (and names the posts it can't: subs NULL)
                return ("(" + ("p.words @@ %s::tsquery OR " if w else "") + SUBS_IDS + ")", ([w] if w else []) + [n])
            if w:
                return "(p.words @@ %s::tsquery OR p.grams @@ %s::tsquery)", [w, g]
            return "p.grams @@ %s::tsquery", [g]
        if k == "near":
            return t(("and", [("term", node[1]), ("term", node[2])]), ctx)
        if k == "not":
            return None
        if k == "field":
            return t(node[2], node[1]) if node[1] in ("content", "author") else None
        parts = [t(n, ctx) for n in node[1]]
        if k == "and":
            parts = [x for x in parts if x]
            if not parts:
                return None
            return "(" + " AND ".join(x for x, _ in parts) + ")", [a for _, xs in parts for a in xs]
        if not parts or None in parts:
            return None
        return "(" + " OR ".join(x for x, _ in parts) + ")", [a for _, xs in parts for a in xs]

    return t(q["node"]) if q["node"] else None


def _cond(node, ctx):
    """the exact test of a node → (sql, params)"""
    k = node[0]
    col = "p.hay" if ctx == "all" else TITLE_COL if ctx == "title" else RAW[ctx]
    if k == "term":
        parts, args = [], []
        w = _word(node[1], ctx)
        if w:
            parts.append("coalesce(p.words @@ %s::tsquery, FALSE)")
            args.append(w)
        subs, sargs = [f"strpos({col}, %s) > 0" for f in _forms(node[1], ctx)], _forms(node[1], ctx)
        n = _sub(node[1], ctx)
        if n:  # a post with subs: the prefix lookup is the substring test; without (no row, or NULL): strpos
            parts.append(SUBS_TEST.replace("{strpos}", " OR ".join(subs)))
            args += [n] + sargs
        else:
            parts += subs
            args += sargs
        return "(" + " OR ".join(parts) + ")", args
    if k == "near":  # the cheap tests first: near() only runs on rows that contain both
        fa, fb = _forms(node[1], ctx), _forms(node[2], ctx)
        if len(fa) != len(fb):  # one term changes under normalisation, the other doesn't: pair each form with each
            pairs = [(x, y) for x in fa for y in fb]
        else:
            pairs = list(zip(fa, fb))
        parts, args = [], []
        for a, b in dict.fromkeys(pairs):
            parts.append(f"(strpos({col}, %s) > 0 AND strpos({col}, %s) > 0 AND near({col}, %s, %s, %s))")
            args += [a, b, a, b, node[3]]
        return "(" + " OR ".join(parts) + ")", args
    if k == "not":
        s, a = _cond(node[1], ctx)
        return f"NOT ({s})", a
    if k == "field":
        return _cond(node[2], node[1])
    parts = [_cond(n, ctx) for n in node[1]]
    return "(" + f" {k.upper()} ".join(s for s, _ in parts) + ")", [x for _, a in parts for x in a]


def to_sql(q):
    """parsed query → (where_sql, params) for Postgres (%s placeholders); the posts table is aliased p"""
    ctx = "title" if q["topic_only"] else "all"
    where, params = (_cond(q["node"], ctx) if q["node"] else ("TRUE", []))
    for lst, neg in ((q["inc"], False), (q["exc"], True)):
        if lst:
            conds = []
            for name in lst:
                conds.append("(p.source_type = %s OR p.website = %s OR p.sid = %s OR p.platform = %s OR p.source = %s)")
                params += [name] * 5
            s = "(" + " OR ".join(conds) + ")"
            where = f"({where}) AND " + (f"NOT {s}" if neg else s)
    pre = prefilter(q)
    if pre:  # the GIN indexes on posts.words and posts.grams first; rows not indexed yet (grams NULL) are always tested
        where = f"({pre[0]} OR p.grams IS NULL) AND ({where})"
        params = pre[1] + params
    return where, params


def rank_sql(q):
    """1 when the post matches the query by words alone (word matches rank before substring-only ones), else 0 →
    (sql, params). NOT parts and FindIn content/author/source count as met (to_sql already tested them)."""
    def r(node, ctx):
        k = node[0]
        if k == "term":
            w = _word(node[1], ctx)
            return ("coalesce(p.words @@ %s::tsquery, FALSE)", [w]) if w else ("FALSE", [])
        if k == "near":
            return r(("and", [("term", node[1]), ("term", node[2])]), ctx)
        if k == "not":
            return "TRUE", []
        if k == "field":
            return r(node[2], node[1]) if node[1] == "title" else ("TRUE", [])
        parts = [r(n, ctx) for n in node[1]]
        return "(" + f" {k.upper()} ".join(s for s, _ in parts) + ")", [x for _, a in parts for x in a]

    if not q["node"]:
        return "1", []
    s, a = r(q["node"], "title" if q["topic_only"] else "all")
    return f"({s})::int", a

