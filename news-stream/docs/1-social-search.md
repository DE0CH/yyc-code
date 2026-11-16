# Daily social-platform search (Galbot first)

Built on 2026-10-13 and pushed to DE0CH/YYC main (commit 66b20dd). The server deploys main by itself within 5 minutes.

## What changed

- **Every stream's social search now runs once a day.** It previously ran hourly on 小紅書 and 抖音 and every 3 hours elsewhere. The timer still fires hourly, but it only searches a platform and query whose last search is 24 hours old. A failed search is retried after 3 hours. Failed searches are not billed.
- **Facebook keyword search for Galbot**, through AnyAPI (`facebook.search_posts`). TikHub has no Facebook endpoints. AnyAPI was one of the three services that found STEM Work's Galbot post in the 2026-10-12 test, without us following that page.
- **Facebook results are filtered.** Facebook's search matches loosely: a live test of 銀河通用 on 2026-10-13 returned 21 posts, almost all about 銀河 (galaxies, Star Wars, anime). So only posts that contain one of the Galbot stream's 17 keywords are kept.
- **Results and run now, by script (Deyao only; the website is read-only, 2026-10-14):** `infra/control.py social STREAM` shows each platform's last search with its post count and any error, a log of every run with its request count and cost at list price, and the newest posts. `tikhub_fetch.py --run --now STREAM` runs all of the stream's queries on every platform now; only one search runs at a time.
- Posts still flow into the stream itself: each one is filed under the stream whose query found it, and the AI relevance check decides whether it stays.

## Projected cost (list prices, 2026-10-13)

- **TikHub:** USD 0.01 per search on 小紅書, 抖音, 快手 and 微信公眾號; 0.002 on YouTube, Threads and Instagram; 0.001 on the other 9 platforms. That is **USD 0.055 per query** across the 16 platforms.
- **AnyAPI Facebook search:** **USD 0.03 per search**, up to 20 posts, when its first source answers. If that source fails, AnyAPI fails over to a backup that charges up to **USD 0.0661** per search. AnyAPI's published figures: from USD 0.03, failover maximum USD 0.0661, median response 17 s.
- **Daily schedule, all 6 streams:** 14 queries on TikHub (USD 0.77) plus Galbot's 3 Facebook searches (USD 0.09) = **USD 0.86 a day, USD 25.80 per 30 days**. If every Facebook search hit the backup, it would be USD 0.968 a day (USD 29.04 per 30 days).
- **Galbot alone:** 3 queries × USD 0.055 + 3 × USD 0.03 = **USD 0.255 a day, USD 7.65 per 30 days**. One button run costs the same USD 0.255 (up to USD 0.363).
- **Before this change** the hourly TikHub schedule would have cost USD 10.64 a day (USD 319.20 per 30 days) once funded.
- AI tagging of the new posts runs on the existing OpenRouter key `yyc-stream` (USD 20/month cap).

## What it needs before results come in

1. **A TikHub balance.** Every TikHub search on 2026-10-13 answered HTTP 402 "Insufficient balance". Most search endpoints refuse TikHub's free daily credit.
2. **A YYC AnyAPI account and key.** The 2026-10-13 test used a free trial key: about USD 0.05 of credit, no account, expires on 2026-10-20. Its credit was spent on one search. The production key goes in the server's `/etc/yyc-stream/app.env` as `ANYAPI_API_KEY`, on an account used only for YYC, so that AnyAPI's dashboard is the expense evidence.
3. Until then, the page shows each platform's error (402 for TikHub, "ANYAPI_API_KEY is not set" for Facebook) and costs nothing.
