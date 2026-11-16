"""Entities to look up (owner, group, role, why, key names for matching, site for social-link scraping)."""
import json, os, re
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) + ""
E = []
def add(owner, group, role, why, keys, site=None):
    E.append({"owner": owner, "group": group, "role": role, "why": why, "keys": keys, "site": site})

# politicians: principal officials and ExCo (gov.hk / ceo.gov.hk lists; matched by name)
for n, k, role in [
    ("行政長官 李家超 John Lee", ["李家超"], "Chief Executive"),
    ("政務司司長 陳國基 Eric Chan", ["陳國基"], "Chief Secretary for Administration"),
    ("財政司司長 陳茂波 Paul Chan", ["陳茂波"], "Financial Secretary"),
    ("律政司司長 林定國 Paul Lam", ["林定國"], "Secretary for Justice"),
    ("副司長 卓永興 Cheuk Wing-hing", ["卓永興"], "Deputy Chief Secretary"),
    ("副司長 黃偉綸 Michael Wong", ["黃偉綸"], "Deputy Financial Secretary"),
    ("副司長 張國鈞 Horace Cheung", ["張國鈞"], "Deputy Secretary for Justice"),
    ("曾國衞 Erick Tsang", ["曾國衞", "曾國衛"], "Secretary for Constitutional and Mainland Affairs"),
    ("楊潤雄 Kevin Yeung", ["楊潤雄"], "Secretary for Culture, Sports and Tourism"),
    ("蔡若蓮 Christine Choi", ["蔡若蓮"], "Secretary for Education"),
    ("謝展寰 Tse Chin-wan", ["謝展寰"], "Secretary for Environment and Ecology"),
    ("許正宇 Christopher Hui", ["許正宇"], "Secretary for Financial Services and the Treasury"),
    ("盧寵茂 Lo Chung-mau", ["盧寵茂"], "Secretary for Health"),
    ("麥美娟 Alice Mak", ["麥美娟"], "Secretary for Home and Youth Affairs"),
    ("何永賢 Winnie Ho", ["何永賢"], "Secretary for Housing"),
    ("孫東 Sun Dong", ["孫東"], "Secretary for Innovation, Technology and Industry"),
    ("孫玉菡 Chris Sun", ["孫玉菡"], "Secretary for Labour and Welfare"),
    ("鄧炳強 Chris Tang", ["鄧炳強"], "Secretary for Security"),
    ("林世雄 Lam Sai-hung", ["林世雄"], "Secretary for Transport and Logistics"),
    ("甯漢豪 Bernadette Linn", ["甯漢豪"], "Secretary for Development"),
    ("丘應樺 Algernon Yau", ["丘應樺"], "Secretary for Commerce and Economic Development"),
    ("楊何蓓茵 Ingrid Yeung", ["楊何蓓茵"], "Secretary for the Civil Service"),
    ("葉劉淑儀 Regina Ip", ["葉劉淑儀"], "ExCo convenor, LegCo member (NPP)"),
    ("林正財 Lam Ching-choi", ["林正財"], "ExCo member"),
    ("湯家驊 Ronny Tong", ["湯家驊"], "ExCo member"),
    ("梁振英 Leung Chun-ying", ["梁振英"], "former Chief Executive, CPPCC vice-chairman"),
    ("林鄭月娥 Carrie Lam", ["林鄭月娥"], "former Chief Executive"),
    ("曾鈺成 Jasper Tsang", ["曾鈺成"], "former LegCo President"),
    ("田北俊 James Tien", ["田北俊"], "Liberal Party honorary chairman"),
    ("何君堯 Junius Ho", ["何君堯"], "former LegCo member"),
    ("譚耀宗 Tam Yiu-chung", ["譚耀宗"], "NPCSC member"),
    ("陳智思 Bernard Chan", ["陳智思"], "former ExCo convenor"),
    ("香港特區政府 HKSAR Government", ["香港特區政府", "香港特区政府", "HKSAR Government", "GovHK"], "HKSAR Government"),
    ("香港政府新聞處 ISD", ["政府新聞處", "新聞處", "news.gov.hk"], "Information Services Department"),
    ("立法會 Legislative Council", ["立法會", "LegCo"], "Legislative Council"),
    ("消防處 Fire Services Department", ["消防處", "Fire Services Department"], "Fire Services Department (宏福苑 fire)"),
    ("房屋署 Housing Department", ["房屋署", "房委會", "Housing Department"], "Housing Department"),
    ("屋宇署 Buildings Department", ["屋宇署", "Buildings Department"], "Buildings Department (宏福苑 renovation)"),
    ("醫院管理局 Hospital Authority", ["醫院管理局", "醫管局", "Hospital Authority"], "Hospital Authority"),
    ("投資推廣署 InvestHK", ["投資推廣署", "InvestHK"], "InvestHK"),
    ("香港金融管理局 HKMA", ["金融管理局", "金管局", "HKMA"], "HKMA"),
    ("證監會 SFC", ["證監會", "Securities and Futures Commission"], "SFC"),
    ("品牌香港 Brand Hong Kong", ["Brand Hong Kong", "品牌香港"], "Brand Hong Kong"),
    ("香港旅遊發展局 HKTB", ["香港旅遊發展局", "Discover Hong Kong", "旅發局"], "Hong Kong Tourism Board"),
    ("選舉事務處 REO", ["選舉事務處", "Registration and Electoral Office"], "Registration and Electoral Office (ECE elections)"),
    ("香港警察 Hong Kong Police Force", ["香港警察", "Hong Kong Police", "香港警務處"], "Hong Kong Police Force"),
    ("民建聯 DAB", ["民建聯", "DAB"], "party"), ("工聯會 FTU", ["工聯會", "FTU"], "party"),
    ("經民聯 BPA", ["經民聯", "BPA"], "party"), ("新民黨 NPP", ["新民黨"], "party"), ("自由黨 Liberal Party", ["自由黨"], "party"),
    ("民主黨 Democratic Party", ["民主黨"], "party"), ("新論壇", ["新論壇"], "group"), ("實政圓桌", ["實政圓桌"], "group"),
    ("新思維", ["新思維"], "party"), ("民主思路 Path of Democracy", ["民主思路"], "group"),
    ("九龍社團聯會", ["九龍社團聯會", "九龍社聯"], "group"), ("新界社團聯會", ["新界社團聯會", "新社聯"], "group"),
    ("香港島各界聯合會", ["港島各界聯合會", "港島聯"], "group"),
]:
    add(n, "politician", role, f"{role}: whose posts are CEHK/FSHK/POL/ECE news", k)

# politicians already followed on Facebook/Instagram (git history: facebook_pages.json / instagram_accounts.json)
wl = json.load(open(REPO + "/watchlist.json"))
have = {e["owner"] for e in E}
for r in wl:
    if r["group"] == "politician" and r.get("kind") in ("legislator", None) and r["platform"] == "facebook" or \
            (r["group"] in ("politician", "kol") and r["platform"] == "instagram"):
        name = r["name"]
        zh = re.match(r"^([一-鿿]{2,4})", name)
        if not zh or any(zh.group(1) in e["keys"] for e in E):
            continue
        add(name, r["group"], r.get("role") or "LegCo member", "followed since 2026-10-12; other platforms", [zh.group(1)])

# media outlets: (name, keys, site)
for n, k, site in [
    ("東方日報 on.cc", ["東方日報", "on.cc", "東網"], "https://hk.on.cc/"), ("信報 HKEJ", ["信報"], "https://www.hkej.com/"),
    ("明報 Ming Pao", ["明報"], "https://news.mingpao.com/"), ("香港01 HK01", ["香港01", "HK01"], "https://www.hk01.com/"),
    ("星島 Sing Tao", ["星島"], "https://www.stheadline.com/"), ("經濟日報 HKET", ["經濟日報", "HKET", "香港經濟日報"], "https://www.hket.com/"),
    ("文匯報 Wen Wei Po", ["文匯報", "文匯"], "https://www.wenweipo.com/"), ("大公報 Ta Kung Pao", ["大公報", "大公文匯"], "https://www.takungpao.com/"),
    ("點新聞 DotDotNews", ["點新聞", "dotdotnews", "DotDotNews"], "https://www.dotdotnews.com/"), ("香港商報", ["香港商報", "商報"], "https://www.hkcd.com.hk/"),
    ("am730", ["am730"], "https://www.am730.com.hk/"), ("晴報 Sky Post", ["晴報"], "https://skypost.hk/"), ("都市日報 Metro Daily", ["都市日報"], "https://www.metrodaily.hk/"),
    ("TVB 無綫新聞", ["無綫新聞", "TVB News", "TVB NEWS"], "https://news.tvb.com/"), ("Now 新聞", ["Now新聞", "Now 新聞", "Now News"], "https://news.now.com/"),
    ("有線新聞 i-Cable News", ["有線新聞", "i-Cable"], "https://www.i-cable.com/"), ("RTHK 香港電台", ["香港電台", "RTHK"], "https://news.rthk.hk/"),
    ("商業電台 881903", ["商業電台", "881903", "叱咤903", "雷霆881"], "https://www.881903.com/"), ("新城電台 Metro Radio", ["新城"], "https://www.metroradio.com.hk/"),
    ("ViuTV", ["ViuTV"], "https://viu.tv/"), ("南華早報 SCMP", ["SCMP", "South China Morning Post", "南華早報"], "https://www.scmp.com/"),
    ("The Standard 英文虎報", ["The Standard", "英文虎報"], "https://www.thestandard.com.hk/"), ("HKFP 香港自由新聞", ["Hong Kong Free Press", "HKFP"], "https://hongkongfp.com/"),
    ("法庭線 The Witness", ["法庭線", "The Witness"], "https://thewitnesshk.com/"), ("獨立媒體 inmediahk", ["獨立媒體", "inmediahk"], "https://www.inmediahk.net/"),
    ("集誌社 The Collective", ["集誌社", "The Collective HK"], "https://thecollectivehk.com/"), ("誌 HK Feature", ["HK Feature", "誌 HK Feature"], "https://hkfeature.net/"),
    ("熱血時報 PassionTimes", ["熱血時報", "PassionTimes"], "https://www.passiontimes.hk/"), ("巴士的報 Bastille Post", ["巴士的報", "Bastille Post"], "https://www.bastillepost.com/hongkong/"),
    ("橙新聞 Orange News", ["橙新聞", "Orange News"], "https://www.orangenews.hk/"), ("香港仔 Lion Rock Daily", ["香港仔", "Lionrock"], "https://www.lionrockdaily.com/"),
    ("紫荊 Bauhinia", ["紫荊雜誌", "紫荊"], "https://bauhinia.org/"), ("中國日報香港版 China Daily HK", ["China Daily Hong Kong", "中國日報香港"], "https://www.chinadailyhk.com/"),
    ("鳳凰衛視 Phoenix TV", ["鳳凰衛視", "鳳凰網", "Phoenix TV"], "https://www.ifeng.com/"), ("經濟通 ET Net", ["經濟通", "ET Net"], "https://www.etnet.com.hk/"),
    ("經濟一週 EDigest", ["經濟一週"], "https://www.edigest.hk/"), ("東周刊", ["東周刊"], "https://www.eastweek.com.hk/"),
    ("明周 MP Weekly", ["明周", "明週"], "https://www.mpweekly.com/"), ("資本雜誌 Capital", ["資本雜誌"], "https://www.capitalfm.com.hk/"),
    ("iMoney 智富", ["iMoney", "智富"], "https://www.imoney.com.hk/"), ("亞洲週刊", ["亞洲週刊"], "https://www.yzzk.com/"),
    ("明報月刊", ["明報月刊"], None), ("成報", ["成報"], "https://www.singpao.com.hk/"), ("Yahoo 新聞 香港", ["Yahoo新聞", "Yahoo 新聞"], None),
    ("灼見名家 Master Insight", ["灼見名家"], "https://www.master-insight.com/"), ("堅料網", ["堅料網"], None), ("圓點Dot", ["圓點"], None),
    ("透視報", ["透視報"], None), ("通視", ["通視"], None), ("Dimsum Daily", ["Dimsum Daily"], "https://www.dimsumdaily.hk/"),
    ("Young Post", ["Young Post"], "https://www.scmp.com/yp"), ("Asia Times", ["Asia Times"], "https://asiatimes.com/"),
    ("BBC 中文", ["BBC News 中文", "BBC 中文"], None), ("RFI 華語", ["RFI 華語", "法廣"], None), ("DW 中文", ["DW 中文", "德國之聲"], None),
    ("Bloomberg Asia", ["Bloomberg Asia", "Bloomberg TV"], None), ("Reuters Asia", ["Reuters Asia"], None), ("Nikkei Asia", ["Nikkei Asia"], None),
    ("FT", ["Financial Times"], None), ("CNA 亞洲新聞台", ["CNA", "Channel NewsAsia"], None),
    ("新華社 Xinhua", ["新華社", "Xinhua"], None), ("CGTN", ["CGTN"], None), ("央視新聞 CCTV", ["央視新聞", "CCTV"], None),
]:
    add(n, "media", "news outlet", "HK outlet / outlet covering HK: its social posts are news and drive discussion", k, site)

# business
for n, k, site in [
    ("港投公司 HKIC", ["港投公司", "香港投資管理", "HKIC", "Hong Kong Investment Corporation"], "https://www.hkic.org.hk/"),
    ("陳家齊 Clara Chan (HKIC CEO)", ["陳家齊", "Clara Chan"], None),
    ("銀河通用 Galbot", ["銀河通用", "银河通用", "Galbot"], "https://www.galbot.com/"), ("王鶴 Wang He (Galbot founder)", ["王鹤", "王鶴"], None),
    ("香港交易所 HKEX", ["香港交易所", "HKEX", "港交所"], "https://www.hkexgroup.com/"), ("滙豐 HSBC HK", ["HSBC", "滙豐"], None),
    ("中銀香港 BOCHK", ["中銀香港", "BOCHK"], None), ("恒生銀行 Hang Seng Bank", ["恒生銀行", "Hang Seng Bank"], None),
    ("渣打香港 Standard Chartered HK", ["渣打", "Standard Chartered"], None), ("友邦 AIA", ["AIA", "友邦"], None),
    ("國泰航空 Cathay Pacific", ["國泰", "Cathay Pacific"], None), ("港鐵 MTR", ["港鐵", "MTR"], None),
    ("長和 CK Hutchison", ["長和", "CK Hutchison"], None), ("李嘉誠基金會", ["李嘉誠"], None),
    ("新鴻基地產 SHKP", ["新鴻基地產", "新地", "SHKP"], None), ("恒基兆業 Henderson Land", ["恒基", "Henderson Land"], None),
    ("新世界發展 New World Development", ["新世界發展", "New World Development"], None), ("太古 Swire", ["太古", "Swire"], None),
    ("領展 Link REIT", ["領展", "Link REIT"], None), ("中電 CLP", ["中電", "CLP"], None), ("港燈 HK Electric", ["港燈", "HK Electric"], None),
    ("香港電訊 HKT", ["香港電訊", "HKT"], None), ("騰訊 Tencent", ["騰訊", "Tencent"], None), ("阿里巴巴 Alibaba", ["阿里巴巴", "Alibaba"], None),
    ("小米 Xiaomi", ["小米", "Xiaomi"], None), ("美團 Meituan / KeeTa", ["美團", "Keeta", "KeeTa"], None), ("京東 JD", ["京東", "JD.com"], None),
    ("香港總商會 HKGCC", ["香港總商會", "HKGCC"], None), ("香港中華總商會", ["中華總商會"], None), ("香港工業總會 FHKI", ["工業總會", "FHKI"], None),
    ("香港中華廠商聯合會 CMA", ["廠商會", "中華廠商聯合會"], None), ("香港地產建設商會 REDA", ["地產建設商會"], None),
    ("香港科技園 HKSTP", ["香港科技園", "HKSTP", "Science Park"], None), ("數碼港 Cyberport", ["數碼港", "Cyberport"], None),
    ("優必選 UBTech", ["優必選", "优必选", "UBTech", "UBTECH"], None), ("宇樹科技 Unitree", ["宇樹", "宇树", "Unitree"], None),
    ("智元機器人 AgiBot", ["智元", "AgiBot"], None), ("商湯 SenseTime", ["商湯", "商汤", "SenseTime"], None),
]:
    add(n, "business", "company / business figure", "relevant to the HKIC, FSHK or Galbot streams", k, site)

json.dump(E, open(B + "/entities.json", "w"), ensure_ascii=False, indent=1)
print(len(E), {g: sum(1 for e in E if e["group"] == g) for g in ("politician", "media", "business", "kol")})
