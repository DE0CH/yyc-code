"""Every WisersOne call the relay may make (Deyao, 2026-10-29: "full feature relay for Wisers … only the read-only
endpoints, do not relay the write endpoints (e.g. bookmarks) … do not relay tracking components"; WiseSearch only).

One row per call, as WisersOne's own app sends it (its bundle main.*.js, the lazily loaded chunks 1/7/27 and
ToolDownloadAction; read 2026-10-29): the resource and the path, the "method" param the app adds, what the call is for,
and what it costs. relay.call() refuses anything that is not a row here, so a write, a tracking call or an outward one
(e-mail) cannot reach Wisers. tests/test_wisers_paths.py checks every row against the bundle's own path constants
(tests/fixtures/wisers_paths.txt) and that the excluded paths below are no row.

cost:
  free      a lookup: no quota code anywhere in the app's handling of it
  search    search/searchresult: one WiseSearch search (the account's search quota)
  derived   a call scoped to a search (tab counts, facets, hot keywords, similar, excerpts, merge counts): its quota cost
            is unknown, so each is logged with charged "unknown", cached per search and made only when someone opens it
  doc       a document's full text: click quota 60003 (the app's canOpenDocumentByQuota)
  image     the image quota gate (checkQuotaExceed {style: img} + writeClickLogByClickType img, which books it): 60004
  download  validateDownloadQuota + the download itself (the download quota)

kind = what the call is, for the calls log: lookup | search | derived | doc | image | download
"""

# (path, resource, the "method" param as the app sends it (None: the app sends none), kind, cost, cache seconds: 0 = never cached here)
ROWS = [
    # the account's feature switches (getUserCustomConfig: enableHotPick, enableReport, maxSearchYears …)
    ("getUserCustomConfig", "CommonService", None, "lookup", "free", 3600),
    # the search form's options
    ("search/mediamessage", "CommonService", None, "lookup", "free", 86400),
    ("getNegativeKeyWords", "CommonService", "get", "lookup", "free", 86400),
    ("search/cannedkeywords", "CommonService", "get", "lookup", "free", 86400),
    ("getHotPicks", "CommonService", None, "lookup", "free", 3600),
    ("getWipPackage", "CommonService", "get", "lookup", "free", 86400),
    ("preference/defaultsearchoption", "CommonService", "get", "lookup", "free", 3600),
    ("label/labels", "CommonService", "get", "lookup", "free", 86400),  # + /<group>/<id>
    ("label/labelautocomplete", "CommonService", None, "lookup", "free", 3600),  # + /<group>
    ("label/autocomplete", "CommonService", None, "lookup", "free", 3600),
    ("label/getLabelNameList", "CommonService", None, "lookup", "free", 86400),
    ("search/keywords", "CommonService", None, "lookup", "free", 3600),
    ("media", "CommonService", None, "lookup", "free", 86400),
    ("medianame", "CommonService", None, "lookup", "free", 86400),
    ("getPublication", "CommonService", "get", "lookup", "free", 7 * 86400),  # + /<id>
    ("search/validateKeywords", "CommonService", None, "lookup", "free", 86400),
    # the search
    ("search/searchresult", "CommonService", None, "search", "search", 0),
    # scoped to a search (criteria always the job's own)
    ("search/searchMergeCount", "CommonService", None, "derived", "derived", 0),
    ("search/getMediaTypeCount", "CommonService", None, "derived", "derived", 0),
    ("search/getSimilarDocuments", "CommonService", None, "derived", "derived", 0),
    ("search/getExcerptByDocId", "CommonService", "post", "derived", "derived", 0),
    ("search/getSearchResultFilter", "CommonService", "get", "derived", "derived", 0),
    ("search/getSearchResultMatchFilter", "CommonService", "get", "derived", "derived", 0),
    ("label/resultfilter", "CommonService", "get", "derived", "derived", 0),  # + /<type>/<id>
    ("label/newscount", "CommonService", "get", "derived", "derived", 0),  # + /<id>
    ("search/getHotKeywordsReport", "CommonService", "get", "derived", "derived", 0),
    ("search/getHotNegativeWords", "CommonService", "get", "derived", "derived", 0),
    ("search/report", "CommonService", None, "derived", "derived", 0),
    ("search/recommendReport", "CommonService", "get", "derived", "derived", 0),
    # reading a document
    ("search/getDocumentByDocId", "CommonService", None, "doc", "doc", 0),
    ("search/getSamePageDocs", "CommonService", None, "lookup", "free", 7 * 86400),
    ("search/checkQuotaExceed", "CommonService", "post", "image", "image", 0),
    # books the image quota (its answer carries quotaLimitCode); called once per document, where the app calls it
    ("search/writeClickLogByClickType", "CommonService", "post", "image", "image", 0),
    # downloads: the quota check, then the options handed to the session, then POST /wevo/download/downloadDocuments
    ("validateDownloadQuota", "CommonService", None, "download", "download", 0),
    ("downloadDocument", "SessionService", None, "download", "download", 0),
]

BY_PATH = {r[0]: r for r in ROWS}
# rows whose path carries more after a "/" (label/labels/<group>/<id>, getPublication/<id> …)
PREFIXED = ("label/labels", "label/labelautocomplete", "label/resultfilter", "label/newscount", "getPublication")

# file URLs on Wisers' host the relay may fetch with the signed-in context (images, the download)
FILES = ("/getFile/getThumbnailImageByDocId/", "/getFile/getImageByDocId/", "/wevo/download/downloadDocuments")

# WiseSearch calls that are deliberately not relayed (tests/test_wisers_paths.py checks none is a row)
EXCLUDED = {
    # writes
    "search/addNewsToMyFolder": "write (folder)", "search/searchToAddMCFolder": "write (folder)",
    "search/delNewsFromMyFolder": "write (folder)", "search/getMCFolders": "only serves the folder write",
    "search/getFolderDocumentList": "account data (folders)", "tagDocs": "write (tags)", "updateTagByBatch": "write (tags)",
    "queryTags": "only serves the tag write", "getTagByDocIdList": "only serves the tag write",
    "preference/searchplanadd": "write (saved search)", "preference/searchplanupdate": "write (saved search)",
    "preference/searchplanremove/": "write (saved search)", "preference/defaultsearchoptionsave": "write (default search)",
    "preference/defaultSearchOptionclear": "write (default search)", "search/mythesaurus": "account data (my thesaurus)",
    "setSavedHighlight": "write (saved highlight)", "getSavedHighlightList": "account data (saved highlights)",
    "preference/searchhistorylist": "account data (search history)", "preference/viewhistorylist": "account data (view history)",
    "preference/searchplanlist": "account data (saved searches)", "preference/mymediaplanlist": "account data (my media)",
    "langswitch": "write (the shared session's content language)", "logout": "ends the relay's session",
    # tracking
    "search/writeClickLog": "tracking (click log)", "eventTracking": "tracking",
    # outward
    "emailDocuments": "outward (Wisers mails third parties)",
    # read the account's one shared "current search" or selection, which every YYC user shares
    "search/getDocumentByIndex": "shared session state (prev/next comes from the job's own items)",
    "search/getCurrentDocument": "shared session state", "search/browseArticle": "shared session state",
    "printDocuments": "shared session state (print is local)",
    "validateSearchDate": "never answers the relay (90 s timeouts, 2026-10-29): the limit is read from getUserCustomConfig", "search/isCurrentDocHasImg": "shared session state (displayStyle.hasImg instead)",
}

# third-party hosts the relay's browser never loads (WisersOne's home page carries Google Tag Manager, GTM-5VC7489)
TRACKING_HOSTS = ("googletagmanager.com", "google-analytics.com", "analytics.google.com", "doubleclick.net",
                  "sensorsdata.cn", "sensorsdata.com", "hm.baidu.com", "cnzz.com", "clarity.ms", "hotjar.com")


def row(path):
    """the row for a path (label/labels/subject/0 → the label/labels row), or None"""
    if path in BY_PATH:
        return BY_PATH[path]
    for p in PREFIXED:
        if path.startswith(p + "/"):
            return BY_PATH[p]
    return None
