// UI vocabulary (Traditional Chinese, Hong Kong usage), the same words as the current pages.
export const SENT = ["negative", "mixed", "irony", "neutral", "positive"] as const;
export const SENT_ZH: Record<string, string> = { positive: "正面", neutral: "中性", negative: "負面", mixed: "褒貶參半", irony: "反諷" };
// each sentiment's colour token (styles/house.css --sent-*): the one hue category of the public pages
export const SENT_VAR: Record<string, string> = { positive: "sent-pos", negative: "sent-neg", mixed: "sent-mixed", irony: "sent-irony", neutral: "sent-neu" };

export const LABELS = ["urgent", "crisis", "issue", "accuse", "rumor", "monitor", "trend", "engaging", "service", "goods",
  "chance", "repute", "rival", "kol", "ask", "review", "praise"] as const;
export const LABEL_ZH: Record<string, string> = {
  monitor: "監察", issue: "議題", trend: "趨勢", service: "服務", chance: "機遇", repute: "聲譽", accuse: "指控",
  engaging: "熱議", kol: "KOL", ask: "訴求", rumor: "傳聞", goods: "商品", rival: "競爭對手", crisis: "危機",
  review: "評價", praise: "讚揚", urgent: "緊急",
};

export const TYPE_ZH: Record<string, string> = {
  News: "新聞", Newspaper: "報章", Magazine: "雜誌", Broadcast: "電視/電台", Official: "官方", Blog: "專欄/網誌", Forum: "討論區", "Social Media": "社交媒體", Video: "影片",
};
export const REGION_ZH: Record<string, string> = { HK: "香港", Macau: "澳門", Mainland: "內地", Taiwan: "台灣", Global: "海外" };
export const ENG_ZH: [string, string][] = [["views", "瀏覽"], ["likes", "讚好"], ["comments", "留言"], ["replies", "回覆"], ["shares", "分享"], ["dislikes", "負評"]];

export const BRAND = "YYC 新聞監察";
export const PENDING = "處理中";
