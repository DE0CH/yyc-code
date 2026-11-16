// The stream this browser last showed (NAVIGATION.md "The stream picker"): cookie `yyc-profile`, so the server sees it
// too and renders a page without ?p= with the same stream the browser will show. It was localStorage until
// 2026-10-20, which only the browser can read: the server picked another stream, and hydration failed on the item page.
// The first render reads it only from the root route's loader data (the server's reading, dehydrated into the page).
import { createIsomorphicFn } from "@tanstack/react-start";
import { getCookie } from "@tanstack/react-start/server";

const NAME = "yyc-profile";
const ID = /^[A-Za-z0-9_-]{1,40}$/;  // a stream id; anything else is ignored
const clean = (v: string | null | undefined) => (v && ID.test(v) ? v : null);

/** the remembered stream as this request (server) or this document (browser) has it */
export const readRemembered = createIsomorphicFn()
  .server(() => clean(getCookie(NAME)))
  .client(() => clean(document.cookie.split("; ").find(c => c.startsWith(NAME + "="))?.slice(NAME.length + 1)));

export function remember(id: string) {
  if (!clean(id) || readRemembered() === id) return;
  document.cookie = `${NAME}=${id}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
}

/** once per browser: the value localStorage held before the cookie, moved over (then the key is gone) */
export function moveFromLocalStorage() {
  try {
    const old = localStorage.getItem(NAME);
    if (old == null) return;
    localStorage.removeItem(NAME);
    const id = clean(JSON.parse(old));
    if (id && !readRemembered()) remember(id);
  } catch { /* private mode, or not JSON */ }
}
