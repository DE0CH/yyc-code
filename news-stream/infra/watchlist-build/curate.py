"""Turn reviewed discovery results into watchlist rows (rows.json). The accept lists below were reviewed by Claude
against the candidates in found.json (name match + size + official badge), 2026-10-14."""
import json, re, urllib.request, urllib.parse, ssl, hashlib, os
B = os.environ.get("WATCHLIST_BUILD", os.path.expanduser("~/artifacts/watchlist/build"))
d = json.load(open(B + "/found.json"))
YT = {13: ["麥美娟 Alice MAK"], 27: ["曾鈺成"], 29: ["何君堯"], 33: ["ISD, Hong Kong SAR Government 香港特區政府新聞處"],
      34: ["香港特別行政區立法會"], 35: ["香港消防處 Hong Kong Fire Services Department"], 38: ["Hospital Authority醫院管理局"],
      39: ["投資推廣署 Invest Hong Kong"], 40: ["香港金融管理局 Hong Kong Monetary Authority"], 42: ["Brand Hong Kong"],
      43: ["Discover Hong Kong"], 45: ["香港警察 Hong Kong Police"], 46: ["民建聯"], 47: ["工聯會", "工聯會立法會議員聯合辦事處"],
      51: ["民主黨 The Democratic Party HK"], 59: ["陳恒鑌"], 60: ["葛珮帆EQ"], 64: ["李慧琼 Starry Lee"], 65: ["陳克勤 (Gary Chan)"],
      66: ["何俊賢 一賢堂 官方頻道"], 68: ["周浩鼎"], 72: ["鄭泳舜Vincent Cheng"], 75: ["Dominic Lee 李梓敬"], 84: ["邱達根 Duncan Chiu"],
      91: ["陳家珮 Judy Chan"], 94: ["陳凱欣 Yan Chan"], 98: ["楊永杰"], 102: ["霍啟剛"], 105: ["何敬康 Adrian Ho"], 108: ["方國珊"],
      123: ["洪錦鉉【敢言時刻】"], 154: ["民政及青年事務局 Home and Youth Affairs Bureau"],
      156: ["衞生署衞生防護中心 Centre for Health Protection, DH"], 158: ["潘焯鴻Jason Poon"],
      159: ["on.cc東網", "東網Money18", "ontv東網電視"], 160: ["信報財經新聞"], 161: ["明報 Mingpao", "明報財經 MP Finance"],
      162: ["香港01", "香港01-普通話頻道 "], 163: ["星島頭條", "星島娛樂"], 164: ["香港經濟日報 HKET"], 165: ["文匯報"],
      166: ["大公報 Ta Kung Pao", "大公文匯全媒體"], 167: ["dotdotnews點新聞", "DotDotNews"], 168: ["香港商報"], 169: ["am730"],
      170: ["Sky Post晴報"], 171: ["Metro Daily 都市日報"], 172: ["無綫新聞 TVB NEWS Official"], 174: ["HOY 78 × 有線新聞 Cable News"],
      175: ["RTHK 香港電台", "香港電台公共事務組RTHK", "香港電台新聞 RTHK News", "香港電台視像新聞 RTHK VNEWS"],
      176: ["商業電台 Hong Kong Toolbar"], 177: ["新城財經台", "新城知訊台"], 178: ["ViuTV"],
      179: ["South China Morning Post", "SCMP Clips"], 180: ["The Standard 英文虎報"], 181: ["Hong Kong Free Press"],
      182: ["法庭線 The Witness"], 183: ["獨立媒體"], 184: ["集誌社 The Collective HK"], 185: ["誌HK Feature"],
      186: ["PassionTimes hk"], 187: ["Bastille Post巴士的報"], 188: ["橙新聞"], 190: ["紫荊雜誌"],
      192: ["鳳凰衛視PhoenixTV Official", "鳳凰網Ifeng"], 194: ["經濟一週 EDigest"], 195: ["東周網 Eastweek.com.hk【東周刊官方網站】"],
      196: ["MPWeekly明周"], 198: ["iMoney Magazine"], 199: ["亞洲週刊 YAZHOU ZHOUKAN"], 203: ["灼見名家 Master Insight"],
      204: ["堅料網"], 206: ["透視報"], 207: ["通視"], 208: ["Dimsum Daily"], 209: ["Young Post"], 211: ["BBC News 中文"],
      212: ["RFI 華語 - 法國國際廣播電台"], 213: ["DW 中文 - 德國之聲"], 216: ["Nikkei Asia"], 217: ["Financial Times"], 218: ["CNA"],
      220: ["CGTN"], 221: ["CCTV中国中央电视台", "CCTV中文"], 226: ["HKEX Group"], 227: ["HSBC Hong Kong"], 228: ["中銀香港 BOCHK"],
      229: ["恒生銀行 Hang Seng Bank"], 230: ["渣打銀行香港Standard Chartered HK"], 231: ["AIA Hong Kong And Macau"],
      233: ["MTR Hong Kong"], 235: ["李嘉誠基金會Li Ka Shing Foundation"], 239: ["Swire Properties 太古地產"], 240: ["Link 領展"],
      241: ["CLP 中電"], 242: ["港燈 HK Electric"], 244: ["Tencent Global"], 245: ["Alibaba Group"], 246: ["小米香港Xiaomi Hong Kong"],
      248: ["JD.com, Inc."], 252: ["香港中華廠商聯合會 Hashtag CMA"], 254: ["HKSTP"], 255: ["Cyberport"], 256: ["UBTECH Robotics"],
      257: ["Unitree Robotics"], 258: ["AGIBOT"]}
IG = {0: ["john.kc.lee"], 1: ["ericchan.cso"], 2: ["paul.mpchan"], 14: ["winnieho.sh"], 17: ["tang.ping.keung"],
      21: ["ingridyeung.hksarg"], 22: ["regina.ip"], 28: ["james__tien"], 35: ["hongkongfireservicesdepartment"],
      37: ["buildingsdepartment"], 38: ["hospitalauthority"], 39: ["investhk.gov.hk"], 40: ["hkmagovhk"], 45: ["hongkongpoliceforce"],
      51: ["democratic_party_hk"], 60: ["elizabeth_quat"], 68: ["chowhoding"], 72: ["chengwingshun_vincent"], 75: ["dominicleehk"],
      102: ["fok_kenneth"], 110: ["vmwkong"], 148: ["csbgovhk"], 149: ["cmab.gov.hk"], 150: ["cstbhk"], 151: ["devb.gov.hk"],
      152: ["eebhksarg"], 154: ["hyabhk"], 155: ["securitybureauhk"], 156: ["centreforhealthprotection"], 162: ["hk01_hk"],
      169: ["am730hk"], 174: ["icable.news"], 176: ["cr881903"], 178: ["viutv"], 179: ["scmpnews"], 181: ["hongkongfp"],
      183: ["inmediahknet"], 188: ["orangenewshk"], 204: ["kinliuhk"], 209: ["youngposthk"], 211: ["bbcchinese"],
      213: ["dw.chinese"], 216: ["nikkeiasia"], 217: ["financialtimes"], 218: ["channelnewsasia"], 220: ["cgtn"],
      229: ["hangsengbank.official"], 235: ["li.ka.shing.foundation"], 239: ["swireproperties"]}
REO = 44  # 選舉事務處: its Instagram elections.gov.hk showed under the LegCo search
SITE_SKIP = re.compile(r"sharer|/tr\?|intent|embed\.js|/share$|Ladies|lifestyle|travel|education|property|beauty|geni\.nvestor|eastweekladies|WeekendWeekly|newmonday|metrohealth|metroeducation|thestandardhk_lifestyle", re.I)

def get(url):
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en", "Cookie": "SOCS=CAI"})
    return urllib.request.urlopen(req, timeout=30, context=ctx).read().decode("utf-8", "replace")

def yt_id(url):
    m = re.search(r"/channel/(UC[\w-]{22})", url)
    if m: return m.group(1)
    f = B + "/cache/ytid-" + hashlib.sha1(url.encode()).hexdigest()
    if os.path.exists(f): return open(f).read() or None
    try:
        t = get(url)
        m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"', t) or \
            re.search(r'"externalId":"(UC[\w-]{22})"', t)
        v = m.group(1) if m else ""
    except Exception:
        v = ""
    open(f, "w").write(v); return v or None

rows = []
def row(e, platform, **kw):
    r = {"group": e["group"], "name": kw.pop("name", None) or e["owner"], "owner": e["owner"], "role": e["role"],
         "platform": platform, "why": e["why"], "since": "2026-10-14"}
    r.update({k: v for k, v in kw.items() if v})
    rows.append(r)

for i, e in enumerate(d):
    for want in YT.get(i, []):
        c = next((f for f in e["found"] if f["platform"] == "youtube" and f["name"] == want), None)
        if c:
            row(e, "youtube", name=f"{c['name'].strip()}", handle=c.get("handle"), id=c["id"],
                url=f"https://www.youtube.com/channel/{c['id']}", evidence=f"YouTube channel search '{c['evidence'].split(': ',1)[1]}': {c['followers']} subscribers" + (", verified" if c["verified"] else ""))
    for h in IG.get(i, []):
        c = next((f for f in e["found"] if f["platform"] == "instagram" and f["handle"] == h), None)
        row(e, "instagram", name=f"{(c or {}).get('name') or e['owner']}", handle=h, url=f"https://www.instagram.com/{h}/",
            evidence="Instagram user search: verified account " + h)
    if i == REO:
        row(e, "instagram", handle="elections.gov.hk", url="https://www.instagram.com/elections.gov.hk/", evidence="Instagram user search: verified account elections.gov.hk")
    for l in e.get("site_links") or []:
        if SITE_SKIP.search(l):
            continue
        ev = "linked from the owner's own website " + (e.get("site") or "")
        ld = re.sub(r"^https?://(www\.|m\.)?", "", l)
        if ld.startswith("youtube.com"):
            cid = yt_id("https://www." + ld)
            if cid: row(e, "youtube", id=cid, url=f"https://www.youtube.com/channel/{cid}", handle=ld.split("/", 1)[1], evidence=ev)
        elif ld.startswith("facebook.com/"):
            row(e, "facebook", url="https://www." + ld.split("?fref")[0].rstrip("/"), evidence=ev)
        elif ld.startswith("instagram.com/"):
            h = ld.split("/")[1]; row(e, "instagram", handle=h, url=f"https://www.instagram.com/{h}/", evidence=ev)
        elif re.match(r"threads\.(net|com)/@", ld):
            h = ld.split("@")[1]; row(e, "threads", handle=h, url=f"https://www.threads.com/@{h}", evidence=ev)
        elif re.match(r"(twitter|x)\.com/", ld):
            h = ld.split("/")[1]; row(e, "x", handle=h, url=f"https://x.com/{h}", evidence=ev)
        elif ld.startswith("weibo.com/u/"):
            uid = ld.split("/")[2]; row(e, "weibo", id=uid, url=f"https://weibo.com/u/{uid}", evidence=ev)
json.dump(rows, open(B + "/rows.json", "w"), ensure_ascii=False, indent=1)
import collections; print(len(rows), collections.Counter((r["group"], r["platform"]) for r in rows))
