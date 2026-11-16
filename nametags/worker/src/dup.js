// The LLM half of the duplicate check (the deterministic half is in Store.addGuest): DeepSeek V4.1 Flash through
// OpenRouter compares a new entry with the event's other live entries. Runs in ctx.waitUntil after the insert; any
// failure (no key, timeout, a bad answer) ends as dup.state "error" for staff to see and never blocks the entry.

export const MODEL = "deepseek/deepseek-v4.1-flash";
export const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";
const TIMEOUT_MS = 20_000;

const SYSTEM = `You check sign-ups to one event for duplicates: the same person registered twice.
You get a NEW entry and the event's OTHER entries (id, name, institution, email, phone).
List every OTHER entry that is probably the same person as NEW. Count as the same name: different case or spacing,
a missing or extra letter, a typo, given and family name swapped, initials, a Chinese name and its romanised form
(Cantonese, e.g. 陳大文 = Chan Tai Man, or Mandarin pinyin), with or without an English name. A similar email or phone
(a typo, another domain with the same local part) and the same institution support it. Different people who only
share a common surname or an institution are NOT duplicates.
For each, give its id and a short reason in Traditional Chinese (Hong Kong), at most 15 characters, e.g. 「姓名相同」,
「中英文姓名相同」,「姓名拼寫相近、同機構」. Answer {"duplicates": []} when there are none.`;

const SCHEMA = {
  type: "object",
  properties: {
    duplicates: {
      type: "array",
      items: { type: "object", properties: { id: { type: "string" }, reason: { type: "string" } }, required: ["id", "reason"], additionalProperties: false },
    },
  },
  required: ["duplicates"],
  additionalProperties: false,
};

// the chat/completions body; reasoning "low" is the lowest effort the model lists (supported_efforts on
// GET /api/v1/models: max, high, low, none; "none" turns reasoning off). require_parameters keeps the request on
// providers that honour the JSON schema.
export function requestBody(entry, others) {
  return {
    model: MODEL,
    messages: [{ role: "system", content: SYSTEM }, { role: "user", content: JSON.stringify({ new: entry, others }) }],
    reasoning: { effort: "low", exclude: true },
    response_format: { type: "json_schema", json_schema: { name: "duplicates", strict: true, schema: SCHEMA } },
    provider: { require_parameters: true },
    temperature: 0,
    max_tokens: 4000,
  };
}

// → [{id, reason}] of ids among `others` (throws on any failure)
export async function llmDuplicates(entry, others, { key, fetcher = fetch }) {
  if (!key) throw new Error("OPENROUTER_API_KEY is not set");
  const r = await fetcher(OPENROUTER_URL, {
    method: "POST",
    headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json", "HTTP-Referer": "https://tags.yyc-internal.com", "X-Title": "yyc-nametags" },
    body: JSON.stringify(requestBody(entry, others)),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!r.ok) throw new Error(`openrouter ${r.status}`);
  const content = (await r.json())?.choices?.[0]?.message?.content;
  let list;
  try { list = JSON.parse(String(content).replace(/^```(?:json)?\s*|\s*```$/g, "")).duplicates; } catch { throw new Error("openrouter: answer is not JSON"); }
  if (!Array.isArray(list)) throw new Error("openrouter: no duplicates list");
  const ids = new Set(others.map((o) => o.id));
  return list.filter((d) => d && ids.has(d.id)).map((d) => ({ id: d.id, reason: String(d.reason || "").trim().slice(0, 30) || "可能重複" }));
}

// the whole background step for one guest: the LLM check, then Store.finishDup (which also auto-accepts)
export async function runDupCheck(store, id, { key, fetcher = fetch }) {
  const input = await store.dupInput(id);
  if (!input) return null;
  if (!input.others.length) return store.finishDup(id, { candidates: [] });
  try {
    return await store.finishDup(id, { candidates: await llmDuplicates(input.entry, input.others, { key, fetcher }) });
  } catch (e) {
    console.error("dup check", id, e.message);
    return store.finishDup(id, { error: e.message });
  }
}
