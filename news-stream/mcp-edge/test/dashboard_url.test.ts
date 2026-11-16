// The details-page links the tools put in their answers (../web-app/src/lib/dashboard_url.ts).
//   npm test   (node --test --experimental-strip-types)
import { test } from "node:test";
import assert from "node:assert/strict";
import * as dl from "../../web-app/src/lib/dashboard_url.ts";

test("posts link to their item page on the main site", () => {
  assert.equal(dl.postUrl(123), "https://newswatch.yyc-internal.com/item/123");
  assert.equal(dl.postByUrl("https://news.test/a?b=1&c=中", "CEHK"),
    "https://newswatch.yyc-internal.com/item?p=CEHK&url=https%3A%2F%2Fnews.test%2Fa%3Fb%3D1%26c%3D%E4%B8%AD");
});

test("數據分析 links keep the page's param order and drop empty params", () => {
  assert.equal(dl.dashboardUrl({ tid: 7, p: "CEHK", v: "hot", from: "2026-10-01", to: "2026-10-07", q: "", sent: null }),
    "https://newswatch.yyc-internal.com/dashboard?v=hot&p=CEHK&from=2026-10-01&to=2026-10-07&tid=7");
  assert.equal(dl.topicUrl(3, { p: "FSHK", from: "2026-10-01", to: "2026-10-07", site: "明報" }),
    "https://newswatch.yyc-internal.com/dashboard?v=hot&p=FSHK&from=2026-10-01&to=2026-10-07&site=%E6%98%8E%E5%A0%B1&tid=3");
  assert.equal(dl.personUrl("Mr A B", { p: "CEHK" }), "https://newswatch.yyc-internal.com/dashboard?v=persona&p=CEHK&a=Mr+A+B");
  assert.equal(dl.themeUrl("政策", { p: "CEHK" }), "https://newswatch.yyc-internal.com/dashboard?v=posts&p=CEHK&theme=%E6%94%BF%E7%AD%96");
  assert.equal(dl.sourceUrl("RTHK", { p: "CEHK" }), "https://newswatch.yyc-internal.com/dashboard?v=posts&p=CEHK&site=RTHK");
  assert.equal(dl.streamUrl("CEHK", "2026-10-21"), "https://newswatch.yyc-internal.com/?p=CEHK&d=2026-10-21");
  assert.equal(dl.streamUrl(), "https://newswatch.yyc-internal.com/");
});

test("reports are on the beta site only", () => {
  assert.equal(dl.reportUrl("/reports/2026-10-21/CEHK/report.pdf"), "https://yyc-beta.deyaochen.com/reports/2026-10-21/CEHK/report.pdf");
  assert.equal(dl.reportUrl("https://newswatch.yyc-internal.com/reports/2026-10-21/CEHK/a.html"), "https://yyc-beta.deyaochen.com/reports/2026-10-21/CEHK/a.html");
  assert.equal(dl.reportUrl(), "https://yyc-beta.deyaochen.com/reports/");
});
