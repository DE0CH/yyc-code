# YYC labels (「標籤」)

Labels (name tags, table cards, signs) for YYC's NIIMBOT M3, printed from a laptop. **Staff ask YYC AI for labels**:
YYC AI draws them and sends them to print through the REST API (`/api/v2`), as its `labels` skill says
(`yyc-ai/deploy/hermit/skills/labels/`). Sending gives a print page, where the staff member connects the printer and prints, or cancels
and tells YYC AI what to change. Labels are drawn from shared **templates**, shown on the templates page; changing a
template needs its lock, held by a person. Separate from the name-tag app (`nametags/`).

- **Address:** https://labels.yyc-internal.com, behind the Access app "YYC 標籤" (the YYC users policy, plus the
  service token `yyc-labels-ai` for YYC AI). Worker `yyc-labels` on YYC's Cloudflare account
  (`news-stream/deploy/cloudflare/tofu/labels.tf`), deployed by `.github/workflows/labels-worker.yml`.
- **Language:** the staff pages are in Traditional Chinese (HK, 書面語); the API and the AI's skill are in English.

```
YYC AI (curl, skill labels) ── egress route labels (+ the person) ──▼
                           Worker yyc-labels ── /api/v2 (src/api.js) ── Store (Durable Object, SQLite):
                                │                                       templates, locks, print requests
                                ├── /: the templates page (each template's four examples)
                                └── /p/<id>: a print page (public/app/page.js) ── Web Bluetooth ── M3
```

## Labels

The printer's own format, made by whoever draws them: **848 × 590 dots** (the printable 72 × 50 mm at 300 dpi), 1 bit
per dot (1 = black), rows of 106 bytes top to bottom, the leftmost dot in the highest bit; 62,540 bytes a label, no
header. The Worker never draws: it stores the bits and the page sends them to the printer as they are.

## Templates

A template is a set of files: `design.json` (the layout), `design.md` (how to draw labels from it: instructions and
code), `examples/1.bin` … `examples/4.bin` (four labels drawn from it, shown on the templates page) and `files/<name>`
(anything else: the drawing code, pictures, a guest list). It is saved whole (`PUT`, multipart), only by the holder of
its **lock**: one person at a time, taken with `POST …/lock` (409 `locked` with the holder and since when), released
with `DELETE …/lock`, or taken over with `POST …/lock/override` (recorded in `lock_overrides`; the previous holder's
next change is refused with `lock_taken_over`, who and when). Locks belong to people, never to a conversation: the
person comes from the request (below).

The templates carried over from v1 (the standard name tag and the Surgical Futures Forum's, 2026-11-04) draw with
`files/render.py`: Python + Pillow, the v1 renderer's layout (text fitted by shrinking to `minScale`, one row or two by
overflow, overrides) ported from Rust; on the forum's 93 guests it chose the same rows as the Rust renderer, with the
same layout to a dot or two at letter edges. Their `design.md` documents the `design.json` format.

## Print requests

`POST /api/v2/prints` with 1–500 labels in one body (stored in parts: a call to the Durable Object carries at most
32 MiB) → a print page `/p/<id>`. The page draws each label from its bits, connects the printer (step 1), prints in
order, one job per label, and reports progress (`POST …/status {printed}`): `waiting` → `printing` → `printed`;
**取消，不打印** sets `cancelled` and asks the staff member to tell YYC AI in the chat what to change. A failed or
stopped run continues from the first label not printed. YYC AI reads the outcome with `GET /api/v2/prints/<id>`.
Printing sends each label the way NIIMBOT's own SDK does on the M3 (2026-11-05): the raw rows in blocks of up to 4096
bytes (`0xa7`), each confirmed by the printer (`0x88`) before the next, a rejected block sent again; row by row
(`0x85`) had no check and printed with rows missing. `worker/test/fixtures` holds what `niimbot.py` sends,
which `lib/print.js` matches byte for byte. Safari and Firefox have no Web Bluetooth: there the page shows the labels
but can't print.

## Who

Staff on the pages are their Access email. YYC AI calls with the service token and the person it acts for, which
only YYC AI's egress sets (it drops a sandbox's own copies): `Aviary-Sandbox-Person` (the gateway's identity
triple). Sending labels
and every template change need a person; reading needs none.

## API

`/api/v2` (`src/api.js`; OpenAPI at `/api/v2/openapi.json`): prints (send, list, one, one label, status), templates
(list, one, one file, save whole, delete, lock, override, release), fonts (the catalogue, and each file brotli-compressed
with `Content-Encoding: br`). Errors answer `{error, message}`, the message saying what to do. `/api/v1/*` answers 410
with a message telling the caller to read the labels skill again, or to ask the user for a new conversation.

- **YYC AI:** egress route `labels` (upstream https://labels.yyc-internal.com, the service token `yyc-labels-ai`
  injected, paths `/api/v1/` and `/api/v2/`; `yyc-ai/deploy/egress/egress.json`, address `http://labels`).

## Fonts

`fonts/font-catalog.py` puts the fonts `fonts/font-catalog.txt` lists (Chinese and English, all OFL, from google/fonts
at a pinned commit) in `worker/public/fonts/`, brotli-compressed, with `catalog.json`. CI builds it only when the list
or the script change; `GET /api/v2/fonts/<file>` sends a file as stored (`curl --compressed`).

## Development

`cd worker && npm test` (the fonts test needs `public/fonts`: `python3 ../fonts/font-catalog.py`); `npm run dev`
serves it on :8787 behind a stand-in for Access (`test/dev-entry.js`, local only; `X-Dev-Service: 1` for YYC AI's
service identity, with `X-Jarvis-Person`).

## History

2026-11-04, v2: the Worker no longer renders. v1 kept designs, guest lists and logos and rendered every label with a
Rust renderer (WebAssembly) on the Worker and the page; v2 takes finished bitmaps, and the designs became templates
(the Store dropped v1's tables on start; the two real designs were carried over as templates by hand).
