"""An item's mark: a traffic light for its sentiment, then at most one remark emoji ("3+3", YYC's boss, 2026-10-14:
展示笑面標示，如果轉用紅黃綠燈標示如何？首先標顏色，然後標備註，例如👀或其他). Telegram only (telegram_alerts.tags):
Telegram has only text, so it gets emoji; the website shows sentiment and label tags instead (Deyao, 2026-10-14).

    light   🟢 positive · 🟡 neutral, mixed or irony · 🔴 negative; none while the item is untagged
    remark  the first that applies, from the AI tagger's labels (ai.LABELS) and the source's type:
            👀 值得留意  urgent, crisis, issue, accuse, rumor or monitor: something to keep an eye on or act on
            🔥 熱議      engaging, trend or kol: many likes, shares or comments, a trending topic, a big account
            📌 重要      an official source (government press releases and news: corpus.classify's "Official")
"""
import corpus

LIGHT = {"positive": "🟢", "neutral": "🟡", "mixed": "🟡", "irony": "🟡", "negative": "🔴"}
LIGHTS = [("🟢", "正面"), ("🟡", "中性"), ("🔴", "負面")]
REMARKS = [  # (emoji, name, the labels that give it), first match wins
    ("👀", "值得留意", {"urgent", "crisis", "issue", "accuse", "rumor"}),  # not "monitor": the tagger gives it to most items
    ("🔥", "熱議", {"engaging", "trend", "kol"}),
    ("📌", "重要", set()),  # official sources, below
]


def mark(it):
    """→ {"light", "remark", "note"} for an item (a stream item or a post); empty strings when nothing applies"""
    light = LIGHT.get(it.get("sentiment"), "")
    labels = set(it.get("labels") or [])
    remark = note = ""
    for emoji, name, these in REMARKS:
        if labels & these:
            remark, note = emoji, name
            break
    if not remark:
        stype = it.get("source_type") or corpus.classify(it)["source_type"]
        if stype == "Official":
            remark, note = REMARKS[2][0], REMARKS[2][1]
    return {"light": light, "remark": remark, "note": note}


def text(it):
    """the mark as one string, e.g. "🔴👀"; "" when there is none"""
    m = mark(it)
    return m["light"] + m["remark"]
