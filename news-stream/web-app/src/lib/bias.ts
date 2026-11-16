// The selection-bias note (Deyao, 2026-10-16 and 2026-10-20): the figures that count posts one per vote (摘要's metrics and trend,
// the daily scores, 趨勢, 作者, 來源, hot terms, profile-wide sentiment shares) depend on which sources and keywords
// YYC follows. One line per view on the pages (lib/BiasNote.tsx), the exports' header line and the API answers say this, in
// the same words as insights.BIAS_NOTE / BIAS_NOTE_EN (tests/test_selection_bias.py checks they agree).
export const BIAS_NOTE = "這些數字取決於我們追蹤哪些來源和關鍵字，未必反映實際情況。";
export const BIAS_NOTE_EN = "These figures depend on which sources and keywords we follow, so they may not reflect what is actually happening (selection bias).";
