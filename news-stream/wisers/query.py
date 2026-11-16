"""WiseSearch criteria from the page's query string, and Wisers' items in the shape the page, yyc-mcp and YYC AI read
(relay.py; Deyao, 2026-10-29: the full WiseSearch, read-only).

criteria(q) takes the query string of a search URL (/app/wisers/search?q=…, the same keys as the relay's api/search):
  q              keyword (quick mode)
  and, or, nand, nor   logic mode: contains all / any, excludes all / any (space-separated words; one of and/or needed)
  range          all | title | content          within   document | paragraph | sentence
  period         today | yesterday | 2 | 3 | 7 | 30 | 182 | 365 | year-1 | custom (from, to: YYYY-MM-DD)
  sort           DESC:date | DESC:score | tracking (needs neg)
  mt, mc, mr     media type / category / region values (comma-separated; none = all)
  tab            the result tab: one media type (WisersOne's tabs: getMediaTypeCount's keys); none = all
  lang           all | cn | en (publication language)        doclang   the result filter's document language
  inc, exc       include / exclude media ids (comma-separated)
  author, column, section      similar 0 = no synonyms      neg   negative categories (comma-separated)
  hot            hot pick ids (comma-separated)               db    database package id
  lab_<tree>     JSON [{id, type, name}] per label tree (subject, listed, industry, group, brand, edu)
  within_kw      JSON [{keyword, scope: content|title}]: search within results (keyWordScope), stacked
  f_labels       JSON [{labelId, labelTypeId}]: the result filter's labels (labelFilterList)
  f_region, f_pubregion, f_important, f_match, f_sensitive     the result filter's other selections
  cluster        5 = merge similar (WisersOne's default) | 0 = off      summary 0 = no excerpts
  page, size     page number, page size (20 | 30 | 50)
Nothing here writes: createSearchHistoryFlag is always false (WisersOne's own default criteria have it false too; the
relay used to send it true on page 1, which wrote YYC's searches into the account's search history).
"""
import json, re, urllib.parse
from datetime import date, timedelta

CRITERIA = {  # what the app sends for a Direct Search (captured from the page, 2026-10-13; defaults as in its store)
    "labelList": [], "keyword": "", "rangeTitle": True, "rangeContent": True, "withinScope": "document",
    "dateFrom": "", "dateTo": "", "mediaCategory": "all", "mediaType": "all", "pubType": "all", "mediaRegion": "all",
    "author": "", "column": "", "section": "", "thesaurusEnable": True, "myThesaurusEnable": False, "clusterType": "5",
    "sortOrder": "DESC:date", "dateRangePeriod": "today", "startIdx": 1, "pageSize": 50, "language": "",
    "negativeKeywordEnable": False, "includePubids": "", "excludePubids": "", "createSearchHistoryFlag": False,
    "labelFilterList": [], "matches": "all", "supportExcerpt": True, "keyWordScope": [], "industries": [],
    "subjects": [], "companies": [], "listedCompanies": [], "brands": [], "hotPickIds": "", "hotPickPubIds": "",
    "myMediaPlanId": "", "wipPkg": None, "negativeCategories": "", "otherLabel": {"edu": []}, "pubLanguage": "all",
    "pubRegion": "", "importantMedia": "", "logicKeyWord": None, "firstVisit": 1}

PERIOD_DAYS = {"2": 1, "3": 2, "7": 6, "30": 29, "182": 181, "365": 364}
SIZES = (20, 30, 50)
# the advanced options' label trees: (key, title, criteria field, tree group, root id) — from the app's code
# (GroupNames; listed companies are the "group" tree under LISTED_CODE_ID 35; education is otherLabel.edu, tree 101)
TREES = [("subject", "主題 Subject", "subjects", "subject", "0"),
         ("listed", "上市公司 Listed Company", "listedCompanies", "group", "35"),
         ("industry", "行業 Industry", "industries", "industry", "0"),
         ("group", "集團／公司 Group/Company", "companies", "group", "0"),
         ("brand", "品牌／產品 Brand/Product", "brands", "brand", "0"),
         ("edu", "教育 Education", "otherLabel.edu", "group", "101")]
GROUPS = {t[3] for t in TREES}


class Bad(ValueError):
    """a query the relay refuses before any Wisers call (the message says which field)"""


def _list(q, k):
    return [v.strip() for x in q.get(k, []) for v in x.split(",") if v.strip()]


def _json(q, k):
    try:
        v = json.loads((q.get(k) or ["[]"])[0] or "[]")
    except ValueError:
        raise Bad(f"{k}: not JSON")
    if not isinstance(v, list):
        raise Bad(f"{k}: not a list")
    return v


def logic_keyword(a, o, na, no):
    """WisersOne's logic mode as one keyword (its NewsSearchAction joins the four fields the same way)"""
    w = lambda s: [x for x in re.split(r"\s+", s.strip()) if x]
    parts = []
    if w(a):
        parts.append(" AND ".join(w(a)))
    if w(o):
        parts.append("(" + " OR ".join(w(o)) + ")" if parts else " OR ".join(w(o)))
    kw = " AND ".join(f"({p})" if " OR " in p and len(parts) > 1 and not p.startswith("(") else p for p in parts)
    if w(na):
        kw += " NOT " + " NOT ".join(w(na))
    if w(no):
        kw += " NOT (" + " OR ".join(w(no)) + ")"
    return kw.strip()


def criteria(q, today=None):
    """the query string (parse_qs) → (WiseSearch criteria, page). Raises Bad for a query WisersOne would refuse."""
    g = lambda k, d="": (q.get(k) or [d])[0].strip()
    today = today or date.today()
    c = json.loads(json.dumps(CRITERIA))
    if any(g(k) for k in ("and", "or", "nand", "nor")):
        if not (g("and") or g("or")):
            raise Bad("邏輯搜尋不可以只有「不包含」的字詞。")
        c["keyword"] = logic_keyword(g("and"), g("or"), g("nand"), g("nor"))
        c["logicKeyWord"] = {"andKeyword": g("and"), "orKeyword": g("or"), "notAndKeyword": g("nand"), "notOrKeyword": g("nor")}
    else:
        c["keyword"] = g("q")
    if not c["keyword"]:
        raise Bad("請輸入關鍵字。")
    size = int(g("size", "50")) if g("size", "50").isdigit() else 50
    c["pageSize"] = size if size in SIZES else 50
    page = max(1, int(g("page", "1"))) if g("page", "1").isdigit() else 1
    c["startIdx"] = (page - 1) * c["pageSize"] + 1
    sort = g("sort", "DESC:date")
    if sort == "tracking":
        if not _list(q, "neg"):
            raise Bad("「追蹤」排序需要先選擇負面新聞類別。")
        sort = "DESC:tracking"
    c["sortOrder"] = sort if sort in ("DESC:date", "DESC:score", "DESC:tracking") else "DESC:date"
    rng = g("range", "all")
    c["rangeTitle"], c["rangeContent"] = rng in ("all", "title"), rng in ("all", "content")
    c["withinScope"] = g("within") if g("within") in ("document", "paragraph", "sentence") else "document"
    c["pubType"], c["mediaCategory"], c["mediaRegion"] = (",".join(_list(q, k)) or "all" for k in ("mt", "mc", "mr"))
    c["mediaType"] = g("tab") if re.fullmatch(r"[\w-]{1,30}", g("tab") or "-") and g("tab") else "all"
    c["pubLanguage"] = g("lang") if g("lang") in ("all", "cn", "en") else "all"
    c["language"] = g("doclang") if re.fullmatch(r"[\w-]{1,20}", g("doclang") or "-") and g("doclang") else ""
    c["includePubids"], c["excludePubids"] = ",".join(_list(q, "inc")), ",".join(_list(q, "exc"))
    c["author"], c["column"], c["section"] = g("author")[:100], g("column")[:100], g("section")[:100]
    c["thesaurusEnable"] = g("similar") != "0"
    c["negativeCategories"] = ",".join(_list(q, "neg"))
    c["negativeKeywordEnable"] = bool(c["negativeCategories"])
    c["hotPickIds"] = ",".join(_list(q, "hot"))
    c["wipPkg"] = g("db") or None
    c["clusterType"] = "0" if g("cluster") == "0" else "5"
    c["supportExcerpt"] = g("summary") != "0"
    for key, _, field, _, _ in TREES:
        sel = [{"labelId": str(x["id"]), "labelTypeId": str(x["type"])} for x in _json(q, "lab_" + key)
               if isinstance(x, dict) and x.get("id") and x.get("type")]
        if field == "otherLabel.edu":
            c["otherLabel"]["edu"] = sel
        else:
            c[field] = sel
    c["keyWordScope"] = [{"keyword": str(x.get("keyword"))[:200], "scope": "title" if x.get("scope") == "title" else "content"}
                         for x in _json(q, "within_kw") if isinstance(x, dict) and str(x.get("keyword") or "").strip()]
    c["labelFilterList"] = [{"labelId": str(x.get("labelId")), "labelTypeId": str(x.get("labelTypeId"))}
                            for x in _json(q, "f_labels") if isinstance(x, dict) and x.get("labelId")]
    for k, field in (("f_region", "mediaRegion"), ("f_pubregion", "pubRegion"), ("f_important", "importantMedia")):
        if g(k):
            c[field] = ",".join(_list(q, k))
    c["matches"] = g("f_match") or "all"
    if g("f_sensitive"):
        c["sensitives"] = g("f_sensitive")
    per = g("period", "7")
    ok = lambda s: bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s))
    if per == "today":
        c["dateRangePeriod"] = "today"
    else:  # every other range goes as an explicit custom range (YYYY-MM-DD), as the app's date picker sends it
        if per == "custom":
            f, t = g("from"), g("to")
            f, t = (f if ok(f) else str(today - timedelta(days=6))), (t if ok(t) else str(today))
            if f > t:
                raise Bad("開始日期不可以遲於結束日期。")
        elif per == "yesterday":
            f = t = str(today - timedelta(days=1))
        elif per == "year-1":
            f, t = f"{today.year - 1}-01-01", f"{today.year - 1}-12-31"
        else:
            f, t = str(today - timedelta(days=PERIOD_DAYS.get(per, 6))), str(today)
        c.update(dateRangePeriod="custom", dateFrom=f, dateTo=t)
    return c, page


# ---- what the page shows for an item

# query parameters that only track the click (who shared it, from where); the ones that name the article stay,
# e.g. WeChat's __biz/mid/idx/sn, Baidu's id, and Xiaohongshu's xsec_token (its web notes refuse to open without it)
TRACKING = re.compile(r"^(utm_\w+|spm|scm|fbclid|gclid|dclid|msclkid|yclid|mc_[ce]id|igshid|igsh|si|ref|ref_src|ref_url|"
                      r"from|isappinstalled|scene|srcid|sharer_\w+|share_\w+|shareid|sharesource|xhsshare|xsec_source|"
                      r"chksm|mpshare|clicktime|enterid|exportkey|pass_ticket|wx_header|vid|nwr_flag|_wv|"
                      r"timestamp|ts|s_trans|s_channel|appuid|apptime|tt_from|utm|track\w*|_ga|_gl|cmpid|ocid|feature)$", re.I)


def clean_url(u):
    """the original's link without click-tracking parameters (and its #fragment, unless it is the app's route)"""
    try:
        p = urllib.parse.urlsplit(u or "")
    except ValueError:
        return u
    if not p.netloc:
        return ""
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    frag = p.fragment if p.fragment.startswith("/") else ""  # "#/article?id=…" is a single-page app's address
    scheme = "https" if p.netloc in ("mp.weixin.qq.com", "weibo.com", "baijiahao.baidu.com") else p.scheme
    return urllib.parse.urlunsplit((scheme, p.netloc, p.path, urllib.parse.urlencode(q), frag))


def marked(s):
    """Wisers marks hits as <font class="highlight">…</font>: [[text, is_hit], …] with every other tag dropped"""
    import html
    out = []
    for i, part in enumerate(re.split(r'<font class="highlight">(.*?)</font>', s or "", flags=re.S)):
        t = html.unescape(re.sub(r"<[^>]+>", "", part))
        if t:
            out.append([t, i % 2 == 1])
    return out


def _num(v):
    try:
        return int(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


ENGAGEMENT = ("view_count", "commentCount", "likeCount", "forwardCount")


def item(x):
    """one search item as the page shows it (every field Wisers sent stays under "raw")"""
    ds = x.get("displayStyle") or {}
    return {"doc_id": x.get("docId"), "headline": x.get("headline") or "", "source": x.get("source") or "",
            "date": x.get("publishDate") or "", "author": x.get("author") or "", "kind": x.get("typeId") or "",
            "pub_type": x.get("pubType") or "", "pub_id": x.get("pubId"), "pub_code": x.get("pubCode"),
            "page": x.get("pageNo") or "", "section": x.get("section") or "", "column": x.get("columnName") or "",
            "words": _num(x.get("wordcount")), "circulation": _num(x.get("circulation")),
            "link": clean_url(x.get("webUrl") or x.get("url") or ""), "excerpt": marked(x.get("excerpt") or x.get("content") or ""),
            "has": {k: bool(ds.get("has" + k.capitalize())) for k in ("text", "img", "website", "video", "audio", "pdf")},
            "cluster_key": x.get("clusterAttrKey"), "similar": _num(x.get("similarCount")),
            "engagement": {k: _num(x.get(k)) for k in ENGAGEMENT if x.get(k) not in (None, "")},
            "translated": x.get("translatedHeadline") or None}


SAME_PAGE = ("docId", "pubType", "pageNo", "publishDate", "pubCode", "headline")


def same_page_criteria(x):
    """search/getSamePageDocs's criteria from a search item (DocumentDetailAction.buildSamePageDocsCriteria)"""
    return {k: (x.get(k) or "") for k in SAME_PAGE} if x.get("docId") else None
