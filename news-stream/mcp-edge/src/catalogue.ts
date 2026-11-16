// Who may use the server: YYC's access catalogue (the store's YYC_CATALOGUE_JSON, one list for every YYC app, Deyao
// 2026-10-28), mounted from Secret yyc-catalogue. `users` may sign in and call the tools, `admins` also read the
// call log; "@domain" = every address there. The same value feeds the Access policy "YYC users" (tofu access.tf), so the
// sign-in step already refuses anyone else; checking it again on every call makes a removal stop that person's
// existing tokens too. The kubelet updates the mounted file within about a minute; it is re-read every 30 s.
import { readFileSync } from "node:fs";
import type { Env } from "./env";

interface Lists { users: string[]; admins: string[] }
let cached: { path: string; at: number; lists: Lists } | null = null;

function lists(env: Pick<Env, "CATALOGUE">): Lists {
  if (cached && cached.path === env.CATALOGUE && Date.now() - cached.at < 30_000) return cached.lists;
  try {
    const c = JSON.parse(readFileSync(env.CATALOGUE, "utf8")) as Partial<Lists>;
    const norm = (l: unknown) => (Array.isArray(l) ? l : []).map(e => String(e).trim().toLowerCase()).filter(Boolean);
    cached = { path: env.CATALOGUE, at: Date.now(), lists: { users: norm(c.users), admins: norm(c.admins) } };
  } catch (e) {
    if (!cached || cached.path !== env.CATALOGUE) throw new Error(`the access catalogue ${env.CATALOGUE} is unreadable: ${(e as Error).message}`);
    console.error("catalogue re-read failed; keeping the last good copy", e);  // a half-written update: the last copy
    cached.at = Date.now();
  }
  return cached.lists;
}

const matches = (list: string[], email: string) => {
  const e = email.toLowerCase(), at = e.lastIndexOf("@");
  return at > 0 && list.some(x => x === e || (x.startsWith("@") && x === e.slice(at)));
};

export const isUser = (env: Pick<Env, "CATALOGUE">, email: string) => matches(lists(env).users, email);
export const isAdmin = (env: Pick<Env, "CATALOGUE">, email: string) => isUser(env, email) && matches(lists(env).admins, email);
/** at start-up: fail fast on a missing or broken file */
export const loadCatalogue = (env: Pick<Env, "CATALOGUE">) => { cached = null; lists(env); };
