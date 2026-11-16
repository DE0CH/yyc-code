# YYC name tags — design (DE0CH/yyc-code `nametags/`)

Staff search the event's guest list on an iPhone, tap a guest, see the name tag, press Print; the tag comes out of
the NIIMBOT M3 (75×50 mm labels) next to Deyao's MacBook. Decisions (Deyao, 2026-10-29): list → tap → preview page
with Print; M3; guest list loaded by Claude for this version (no in-app upload); no printed-status tracking;
TestFlight internal group (Deyao only); no deadline.

## Parts

```
iPhone app (SwiftUI)  ──HTTPS, cf-access-token: <CF_Authorization>──▶  tags.yyc-internal.com
                                                                        Cloudflare Access app "YYC 名牌報名"
                                                                        (same staff policy as "YYC AI")
                                                                        ──▶ Worker yyc-nametags (+ Durable Object Store)
MacBook: niimbot.py serve  ──HTTPS, Access service token, polls every 2 s──▶  /api/bridge/*
          └─ BLE ─▶ NIIMBOT M3
```

### Worker `yyc-nametags` (YYC's Cloudflare account, `nametags/worker/`, JS/TS, deployed by a GitHub workflow with
the repo secrets YYC_CF_API_TOKEN / YYC_CF_ACCOUNT_ID like `yyc-web`)
Hostname: proxied DNS record + Workers route in news-stream/deploy/cloudflare/tofu (like the others there);
the Access app there too (policy `users`, plus a service-token policy for the bridge). Every request is behind Access; the Worker still verifies `Cf-Access-Jwt-Assertion` (team domain + this app's AUD)
and reads the email from it. Bridge calls carry an Access service token (common_name in the JWT).

- `GET  /api/guests` → `{"guests":[Guest…], "updated": ISO}` from the Durable Object (SQLite storage; the
  yyc-edge token has no D1 permission, a DO deploys with the script).
  `Guest = {id, name /*col I*/, institution /*col L*/, salutation, surname, firstname, title, email, mobile,
            status /* "Sent" | "Withdrawn" | null */, table /* null for now */}`
- `GET  /api/app-signin?state=S` → 302 `dev.de0ch.yycnametags://signed-in?state=S#token=<CF_Authorization cookie>`
  (the YYC AI router's `/api/jarvis/app-signin` pattern; state must be 16–128 url-safe chars).
- `GET  /api/printer` → `{"online": bool, "lastSeen": ISO|null, "printer": "M3", "error": str|null}`
  (online = bridge polled within 10 s).
- `POST /api/print` body = PNG (image/png, 1-bit, exactly 848×590), query `?guest=ID` → `{"id": jobId}`.
  Stores the job in the DO (id, created, by_email, guest_id, png BLOB, status queued|printing|done|failed, error).
- `GET  /api/print/:id` → `{"id","status","error"}`.
- Bridge (service token only): `GET /api/bridge/next` → 204, or 200 PNG with headers `X-Job-Id`; marks it
  printing. `POST /api/bridge/jobs/:id` `{"status":"done"|"failed","error":str|null}` (a failed job's error is
  Chinese text for staff). Every bridge call also records the heartbeat and `X-Printer-Error`: UTF-8 text
  percent-encoded (Python `urllib.parse.quote`, e.g. 碳帶已用完), sent on every call while the last job failed and
  dropped after a success; the Worker decodes it, and `/api/printer` `error` is the latest value (null when absent).
  A job still `printing` 120 s after the bridge took it is marked failed (「打印機沒有回應」).
- `PUT /api/admin/guests?event=ID` (service token only) body `{"guests":[Guest…]}` replaces the list; `nametags/import.py XLSX`
  reads the sheet (A id, F status, H salutation, I name, J surname, K firstname, L institution, M title, N email,
  O mobile; blank rows skipped) and sends it (guest data never goes into git).
- Errors are `{"error": str}` with 400/403/404/413/415; a request whose Access token fails the check gets 403.
- The Access app's AUD is tofu output `aud.nametags`, set as `ACCESS_AUD` in `worker/wrangler.jsonc`. `/api/guests`,
  `/api/printer` and the print endpoints also accept the service token (used by the import and the live checks);
  `/api/app-signin` is staff only.

### iOS app (`nametags/ios/`, SwiftUI, iOS 17+, bundle `dev.de0ch.yycnametags`, display name 「名牌」)
UI text in Traditional Chinese (HK) (YYC CLAUDE.md: staff preference); guest data shown as it is in the sheet.
- Sign-in: ASWebAuthenticationSession on `/api/app-signin?state=…`, callback scheme `dev.de0ch.yycnametags`;
  token in the Keychain; sent as header `cf-access-token`; a 401/403 or Access login HTML → sign in again.
- Main screen: title, printer status square (■ online / □ offline), search field, count line, list rows
  (name, institution, title; Withdrawn shown muted with the word "Withdrawn"). Tap → detail.
- Detail: the tag preview (the same SwiftUI view that is rendered for printing), the guest's other fields, a
  Print button → states: sending, queued, printing, done, failed (+ error text).
- Search (local, instant, all fields): case/diacritic-insensitive substring over every field; tokens in any order;
  CJK input converted to romanisations and matched against the romanised names: Jyutping, HK government
  (e.g. 陳 → Chan, 郭 → Kwok, 張 → Cheung, 黃 → Wong/Huang), Hanyu Pinyin (黃 → Huang), with each guest name also
  indexed by the Jyutping/pinyin syllables that romanised names usually come from. Also matches initials
  ("kco" → Ki Cheung Otto). Data: bundled table generated from Unihan (kCantonese, kMandarin) + a
  Jyutping → HK-romanisation syllable map; generator script and tests in the repo. Implemented as the Swift package
  `ios/Packages/RomanSearch` (`GuestSearch(guests).search(query)`; its README has the rules, sources and licences).
- House style (deyao-web-design): square corners, flat, ink on white/grey, colour only for meaning, no motion.
- Source: `nametags/ios/` (XcodeGen `project.yml`; `xcodegen generate` writes the Xcode project, not in git). The tag
  is `NameTags/Tag/NameTagView.swift` (preview and print), the 1-bit PNG encoder `Tag/PNG1Bit.swift`; search is the local
  package `Packages/RomanSearch`.
- Test hooks (launch arguments, used only by the CI replica): `-apiBase URL` points the app at another server,
  `-testToken T` signs it in with T instead of the web sign-in, `-scanCode C` feeds C to the scanner as if the camera
  read it (the simulator has no camera).

### Tag image (rendered in the app, 848×590 px at 300 dpi = 72×50 mm, 1-bit)
Name (col I) large and centred, institution (col L) below it, centred, smaller; table number bottom-right when
present (field not in the data yet). Long text shrinks to fit, then wraps to two lines. Matches the sample
"Jing HUANG / Ministry of Service and Technology" layout.

### Printing: no consumer now
The Mac bridge (`niimbot.py serve` on Deyao's MacBook) was a proof of concept and was removed from the code
(Deyao, 2026-11-03); the `/api/bridge/*` endpoints stay for now, with nothing polling them, until the app gets its
own printing path. Staff print labels with the separate label app (`labels/`, Web Bluetooth from a laptop).

### CI (`.github/workflows/nametags-*.yml`)
- worker: test + `wrangler deploy` on push to `nametags/worker/**`.
- ios: on push to `nametags/ios/**`: build the Release app for a simulator matching Deyao's iPhone, run Maestro
  flows (sign-in screen; with a test token: search 黃 finds Huang/Wong guests, open detail, print → fake bridge),
  record video as an artifact; only then unsigned archive → cloud-signed export → TestFlight upload.

## Registration → QR → print (Deyao, 2026-10-30)

A guest registers on a public form, staff approve (or the event auto-accepts), the guest gets a QR code, staff scan
it in the app and the tag prints. Decisions: our own form (replaces the Tencent questionnaire); fields name,
institution, email, phone; Traditional Chinese + English everywhere; approval mode per event (auto / review);
rejected guests get no message; every sign-up is checked for duplicates by DeepSeek V4.1 Flash (lowest reasoning
effort) through OpenRouter; likely duplicates are flagged for staff, who approve, reject, or merge (staff pick the entry
that survives); alerts only as counts on the staff page; messages = a confirmation with the QR when accepted and a
reminder with the QR the day before. **Sending is off for now** (TODO: email from events@yyc.hk — yyc.hk mail is on
Microsoft 365, DNS at domain.net.hk; WhatsApp through Meta's Cloud API on a separate YYC number): every message is
written to the outbox with status `off` and shown on the staff page. On the day a scan button in the app reads the
QR and prints at once; name search stays.

Worker secrets (set once with `wrangler secret put` from a session; the deploy workflow keeps them): `OPENROUTER_API_KEY`
(store `yyc` YYC_NAMETAGS_OPENROUTER_KEY, OpenRouter key yyc-nametags) and `TURNSTILE_SECRET` (tofu output
`nametags_turnstile_secret`).

### Hosts
- `tags.yyc-internal.com` (behind Access, as before): the API below and the **staff page** at `/` (and `/staff/*`).
- `register.yyc-internal.com` (NO Access app; same Worker, route `register.yyc-internal.com/*`): only the public
  routes below. Any other path → 404. Cloudflare Turnstile on the form (widget "yyc-nametags-register";
  `TURNSTILE_SITEKEY` var, `TURNSTILE_SECRET` secret). The Worker picks routes by `Host`; the Access JWT check runs
  only on the tags host.

### Data (Durable Object Store, SQLite)
- `events`: `{id /*slug, [a-z0-9-]{3,40}*/, name, nameEn|null, date /*YYYY-MM-DD*/, venue|null, mode: "auto"|"review",
  open: bool /*form accepts sign-ups*/, created ISO}`. The guest list loaded on 2026-10-29 becomes event
  `surgical-futures-forum` (name "Surgical Futures Forum", date 2026-11-09, mode review, closed; migration on first
  start: existing guests get that event).
- `guests` (= registrations): the old Guest fields plus `event`, `source: "import"|"form"|"staff"`,
  `state: "pending"|"accepted"|"rejected"|"merged"`, `mergedInto|null`, `created ISO`, `code` (QR code, see below),
  `dup: {state: "none"|"checking"|"flagged"|"cleared"|"error", candidates: [{id, reason /*zh-HK short text*/}]}`.
  Imported guests are `accepted`, `dup.state` `none`, id `<event>:<sheet id>` (ids are unique across events); form
  and staff entries get a random id. A staff action sets `by`/`at` on the guest; auto-accept records `by: "auto"`.
- `outbox`: `{id, guest, event, kind: "confirmation"|"reminder", channel: "email"|"whatsapp", to, subject|null, body,
  status: "off"|"sent"|"failed", created ISO}`. One row per channel the guest has an address for.

### Rules
- Submit (form): validate (name 1–100, institution 0–200, email valid, phone 6–20 digits after stripping `+ -()` and
  spaces; all four required except institution), Turnstile check, then insert `pending`, then run the
  duplicate check in `ctx.waitUntil`:
  1. deterministic: same normalised email (lower-case, trimmed) or phone (digits only, HK numbers with or without 852)
     as another non-rejected, non-merged guest of the event → flagged at once (reason 「相同電郵」/「相同電話」).
  2. LLM: OpenRouter `deepseek/deepseek-v4.1-flash`, `reasoning: {effort: "low"}` (the lowest effort the model lists:
     max, high, low, none; "none" turns reasoning off), `provider.require_parameters`, `response_format` JSON schema `{duplicates: [{id, reason}]}`, given the new entry and the event's other
     non-rejected, non-merged entries (id, name, institution, email, phone). Names with case, spacing, a missing/extra letter,
     Chinese vs romanised forms count. Secret `OPENROUTER_API_KEY`. Timeout 20 s; failure → `dup.state = "error"`
     (shown to staff; never blocks).
  Then: flagged → stays `pending` for staff (also in auto mode). Not flagged → auto mode: `accepted` (+ confirmation
  to the outbox); review mode: stays `pending`.
- Staff accept → `accepted` + confirmation in the outbox. Reject → `rejected`, no message. Merge `A into B` → A
  `merged` (`mergedInto` B), B keeps its own state and fields; staff choose which is B. "Not a duplicate" → `dup.state
  = "cleared"`. Every staff action records `by` (email) and `at`.
- `code`: 22-char random url-safe id per guest, unique. QR content = `https://register.yyc-internal.com/r/<code>`.
- Reminders: a Worker cron (hourly) writes a `reminder` for every accepted guest of an event whose date is tomorrow
  in Asia/Hong_Kong, once per guest.
- Guest list for the app = `accepted` guests of the event only.

### Public routes (register host)
- `GET /e/:event` → form page (zh-HK + English, house style, phone-first). Closed/unknown event → a page saying so.
- `POST /e/:event` (form-encoded: name, institution, email, phone, cf-turnstile-response) → 303 to `/r/:code`;
  errors re-render the form with messages.
- `GET /r/:code` → status page: pending 「已收到報名，待確認」/ accepted: the QR (inline SVG) + name + event /
  rejected or merged: 「報名未能確認」 without detail. `GET /r/:code/qr.svg` → the SVG (accepted only, else 404).

### Staff API (tags host; Access staff email, or the service token where noted)
- `GET /api/events` → `{events:[Event + formUrl + counts {pending, flagged, accepted, rejected, outbox}]}` (flagged =
  pending or accepted with `dup.state` flagged). `GET /api/events/:id/qr.svg` → the form link's QR.
  `POST /api/events` `{id, name, nameEn?, date, venue?, mode, open}` → Event; `PATCH /api/events/:id` (any of those
  but id) → Event. 409 on an existing id.
- `GET /api/events/:id/guests?state=…` → `{guests:[Guest…]}` (all states when no `state`). Staff only.
- `POST /api/guests/:id/accept` · `/reject` · `/not-duplicate` · `/merge` `{into: ID}` → the updated Guest
  (merge returns `{merged: Guest, into: Guest}`); 409 when the action doesn't apply to its state. Accept: pending or
  rejected; reject: pending or accepted; not-duplicate: `dup.state` flagged or error; merge: both pending or accepted,
  same event. A rejected or merged entry is dropped from other entries' candidates (a flag left empty → cleared).
- `POST /api/events/:id/guests` `{name, institution, email, mobile}` → staff-entered guest (`source: "staff"`,
  accepted at once, dup check still runs and may flag it; email and mobile optional).
- `GET /api/events/:id/outbox` → `{messages:[Outbox…]}` newest first.
- App: `GET /api/guests?event=ID` → `{guests, updated, event: Event|null}` accepted only; without `event` → the most recent event by
  date that is today or later, else the latest (so old builds keep working). `GET /api/checkin?code=C` → `{event,
  guest}` for an accepted guest (404 `{"error":"找不到此二維碼"}` otherwise; 409 `{"error":"此報名未確認"}` when pending,
  rejected or merged). `POST /api/print?guest=ID` unchanged. Service token also allowed on these.
- `PUT /api/admin/guests?event=ID` (service; `event` required, 404 unknown) replaces that event's imported guests
  (`source: "import"`) only; one already there keeps its code, state and history (`import.py XLSX --event ID`).

### Staff page (`/` on the tags host, one HTML page + JS served by the Worker)
Event list with counts → create/edit event (shows the public form link and its QR for posters) → an event's
registrations, tabs 待處理 (pending, flagged first) / 已確認 / 已拒絕 / 訊息 (outbox); each pending card: fields,
duplicate candidates side by side with the reason, buttons 確認 / 拒絕 / 不是重複 / 合併 (pick which entry stays).
Counts are the only alerts. zh-HK UI, house style (deyao-web-design), phone and desktop.

### App
Main screen gains an event picker (default as `/api/guests` without `event`) and a 掃描 button: camera
(AVFoundation QR) → `GET /api/checkin` → the tag renders and prints at once (same job flow and states as Print), with
an error line for 404/409. Maestro flow with the stub serving a code.
