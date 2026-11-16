# WisersOne (慧科 Wisers)

Researched 2026-10-13 from public pages only; nobody has logged in yet.

## Key points for YYC

- **What it is:** WisersOne, from Wisers Information Limited (慧科訊業), is Hong Kong's long-standing media-monitoring database: print, web, TV/radio and social media, with full text from 1998.
- **Search billing:** the colleague says searches are billed or limited. No public Wisers page mentions a per-search charge or a search quota. The quotas that the public manuals describe apply to monitoring (articles collected), not to search. So the limit is probably in YYC's contract, which isn't public. The account's own pages will show it once we can log in, and until then we treat every search as costly.
- **One user at a time:** the terms allow use "on any single personal computer terminal at any one time", and the login errors include "User over limit". Logging in with the colleague's account can therefore end her session or be refused while she is using it, so a login from our side has to be agreed with her and timed. (Since 2026-10-21 the account is dedicated to the relay by company decision: no one else signs in to it; README "WiseSearch 轉接".)
- **Login fields:** Group ID (客戶組別), Username (用戶名稱) and Password, plus an image captcha at login. The captcha is solved at login time (2Captcha), so it can't be collected in advance.
- **Automation without scraping:** WisersOne can e-mail real-time keyword alerts and export up to 100,000 monitoring records to Excel. Wisers also sells a separate Data Feed API, with no public price. Alerts e-mailed to bot@deyaochen.com would feed the clone without any searches.


## 1. Company and coverage
- Wisers was founded in Hong Kong in 1998 with technology from CUHK. It claims more than 3,000 customers. https://www.wisers.com/aboutwisers/
- WisersOne is "an AI-powered SaaS platform for PR, marketing & corporate communications… across print, digital & social media." Product lines under WisersOne: WiseSearch, WiseMonitor, WiseBI, WisersAnalytics. https://www.wisers.com/
- Source counts (the figures differ by page and year):
  - Print: 2,000+ mainstream media in Greater China plus 900+ from overseas. Websites, forums, blogs and news apps are crawled by 1,000+ servers and updated every 15 minutes. Covers 300+ TV/broadcast programmes, Weibo (it calls its access "highest access to whole business portal of Sina Weibo"), WeChat official accounts, YouTube/Youku/Bilibili/Douyin, and Toutiao/Baijiahao and other self-media. Covers "95% mainstream media in Hong Kong and Taiwan", 180+ mainland cities and 20+ countries. https://www.wisers.com/media-partnership/
  - WiseMonitor: "85 billion omni-media big data", "2500+ News/Websites", 20+ countries. https://www.wisers.com/wisemonitor/
  - HK government Smart LAB listing: "over 80 billion of historical data and 570,000 sources", covering print, social media, websites and forums. https://www.smartlab.gov.hk/en/ai_solutions/a-0038
  - Listen2hk (Wisers' HK social site): "over 790,000 data sources… Facebook, Instagram, YouTube, Telegram, Twitter and forums, blogs, print". https://www.listen2hk.com/en-about-us
  - WiseSearch: 500+ Greater China newspapers, magazines, weeklies, websites and newswires; "數百億條數據" (tens of billions of records) since 1998. https://www.wisers.com/zh-hant/wisesearch/
- Archive depth: full text from 1998 onward. Sources: https://www.lib.polyu.edu.hk/databases/wisenews-%E9%9B%BB%E5%AD%90%E5%89%AA%E5%A0%B1-1998 and https://libguides.lib.hkmu.edu.hk/newsarticles/database-guide-wisersone
- Social platforms:
  - **Facebook, Instagram, YouTube: confirmed.** The WiseSocial/KOL module lists "Instagram, Facebook, YouTube, WeChat, Weibo" (https://www.wisersone.com/public/manuals/WisersOne_UserGuide_en-zh.pdf). Wisers' own reports cite Facebook, Instagram, YouTube, Telegram, Twitter and discussion boards (https://www.wisers.com/blog/1201/big-data-report-on-covid-19-in-the-first-three-quarters-of-2020/).
  - **X/Twitter and Telegram: named** in Wisers' own reports and on Listen2hk (links above).
  - **LIHKG and discuss.com.hk: shown** as sources in the Listen2hk feeds (https://www.listen2hk.com/en-home).
  - **Threads and Xiaohongshu: unknown.** No Wisers page found names either one. The Threads hits found belong to competitors (QSearch, K-Matrix).
  - The search filter's media types are: online media, Weibo, WeChat, print, APP, forum, blog, short video/livestreaming, long video, radio/TV, e-commerce, customer review, others. "Media types that are not opened [for the GID] are not displayed", so what a user sees depends on their contract. https://library.hksyu.edu/lib/wp-content/uploads/2026/03/Media-Center-Userhandbook-Eng-3-1.pdf
- Languages: the platform UI is in English, Traditional Chinese and Simplified Chinese (the login page has an EN/繁/简 switch, from the login bundle). WiseMonitor claims "130+语种" (languages) with AI pre-translation. http://www.wisers.com.cn/product/qqyqgljfx/20.html

## 2. Products and login
- **The login a HK PR person uses is the Wisers Centralized Login at `https://login.wisers.net/?clang=en-zh&fromUrl=https%3A%2F%2Fwww.wisersone.com&product=wiseone`.** The "Login" button on wisers.com links there (https://www.wisers.com/). After login the user is redirected to www.wisersone.com. The manual says "Login address: www.wisersone.com or wisers.one… Enter into the system via the group name, user name and password provided by Wisers or administrator" (manual PDF above). After logging in, a 3x3 grid switches between the subscribed modules: WiseSearch (search), WiseObserver (browse), WiseMonitor/Media Center (monitoring), WiseBI.
- The page is a JavaScript single-page app with no server-rendered `<form>`. The fields were read from its JS bundle (`/main.6350e294d6b1db69318d.chunk.js` plus the lazy chunks):
  1. **Group ID**: UI label "Group ID" (客戶組別); request key `groupId`.
  2. **Username**: UI label "Username" (用戶名稱); request key `userId`. In the code the variable is `username`.
  3. **Password**: request key `password`.
  4. **Captcha**: an image captcha. It is fetched by `POST /api/getCaptcha`, which returns `{id, image}`, and checked by `POST /api/verifyCaptcha {id, code}` before login. It is **shown unless the browser has a saved password for that group in localStorage** (`hideCaptcha = hasCachePassword || type===<wechat-bind>`). A fresh browser therefore always gets a captcha. Error 13242 means "captcha error".
  5. **Remember Me**: checkbox, request key `rememberMe`, checked by default.
  - Alternative: **WeChat login** by QR code (`/api/getUUIDAndQRCode`, `/api/listenUserScan`, `/api/loginByWechatScan`). This only works if the account was bound to WeChat beforehand.
  - **IP login** (`/api/loginByIp {groupId}`) exists for groups with IP-based access (`ipAccessType`). University libraries use this, e.g. HKBU's "on-campus access only".
  - The login call is `POST /api/login {groupId, userId, password, rememberMe}`. It returns `redirectPage`, and the user may be forced into WeChat binding (`forceWeChat`).
- Legacy **WiseNews**: `https://wisenews.wisers.net/wisenews/index.do`. It is a plain server-rendered form: `POST /wisenews/login.do` with fields `groupId` (Group ID, maxlength 20), `userName` (User ID, maxlength 65), `password` (maxlength 20) and `rememberLogin` (checkbox, value 1). It has no captcha. It also has a "kick off" link (`/wisenews/kickoff.do`) that ends an existing session. WisersOne is "formerly WiseNews" (PolyU link above), so this is probably not what the colleague uses.
- Other hosts: `ws1.wisers.net` and `wisesearch.wisers.net` do not resolve. `www.wisers.net` fails TLS.

## 3. Billing and limits
- **No public pricing.** Smart LAB lists the pricing model as "Hybrid – base subscription fee + additional cost per user", with a free trial (https://www.smartlab.gov.hk/en/ai_solutions/a-0038). Sales go through annual subscriptions and tenders, e.g. University of Macau, 12 months (https://pct.admo.um.edu.mo/tender/216770/). One price point found: Taiwan's Executive Yuan paid NT$840,000 for one year of news-clipping search in 2018 (https://ezbid.tw/detail/3/eyis10607).
- **Per-search charges or search quotas: none found.** Search appears to be included in the subscription. Search allows up to 10 comma-separated keywords and up to 20 named media per query (HKSYU handbook).
- **Quotas apply to monitoring, not to search.** "Quota is the total number of articles that can be entered on the platform" for monitoring items. There are three quotas (article volume, video-subtitle analysis, interaction updates). They work "like mobile phone traffic": top-ups can be bought, and deleting items does not free quota. At the cap the navigation tree locks and becomes read-only (HKSYU handbook §5.2.10.4). The number of monitoring items is capped per group (WisersOne manual).
- **Seat and session limits** come from the login error codes in the bundle: `10009` "User over limit", `10010` "Up to limitation of Group ID access", `10006` "IP out of range", `20001` "Not allow to login". Repeated wrong passwords lock the account. WiseNews has a session "kick off" (above), which suggests one concurrent session per user. The exact numbers are in the customer's contract and are unknown.
- **Terms** (WiseNews Terms of Use; the WisersOne terms are the customer agreement, which is not public): use is on "any single personal computer terminal at any one time for your sole and exclusive use". Users may make a single printout or download per item on an insubstantial basis, for research only, not for commercial exploitation or redistribution. Downloading, storing, reproducing etc. are prohibited "except as otherwise permitted in the General Terms". The user is responsible for the confidentiality of their ID and password. https://wisenews.wisers.net/WISENEWS-SHARED/ws/docs/terms.en.htm
- No explicit "no scraping/robots" clause was found in public text. The single-terminal and no-redistribution terms make credential sharing and automated bulk extraction a contract risk.

## 4. Legitimate export, alert and API paths
- **Excel download**: up to 100,000 records per monitoring download, as EXCEL with chosen fields (HKSYU handbook §5.2.17). Charts export to PDF or Excel (manual).
- **Article actions**: email, share (H5 page), print, download (https://hkbu.libguides.com/c.php?g=941319&p=6815441).
- **Email alerts**: the Alert Center does "real-time delivery of concerned news via e-mail" on keyword, sentiment and media filters (manual; https://www.wisers.com/real-time-alerts/). Hot-list subscriptions and scheduled reports exist (Report Center).
- **API**: wisers.com advertises "API Integration". Wisers' company deck lists a "Data Feed API – customized raw media data plus AI-processed structured data feed API, connecting to customer's big data platform" and "Cloud AI API services" (https://www.smartlab.gov.hk/image-file/smartlab/prod/assets/ai_solutions/20250508114805741392_Wisers%20Company%20Introduction_Presentation%20Deck.pdf). It is sold separately; there are no public docs or prices.
- RSS: unknown; none mentioned anywhere.

## 5. Rate limits, IP and sessions
- Group-level IP ranges can restrict logins (error 10006 and the IP-login mode).
- Per-user and per-group access limits exist (10009 and 10010).
- Login timeouts: 10003 and 40000.
- The login page loads Sensors Data analytics, which identifies `gid@uid`.
- No published API or request rate limits were found (unknown).
