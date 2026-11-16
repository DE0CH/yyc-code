// The clock a render reads ("today", "this year", 處理中's 30 minutes). While the browser hydrates the server's page it
// is the server's clock (the root loader's `now`, dehydrated into the page), so the first render matches the server's
// HTML even across midnight or a 30-minute mark; after hydration it is the browser's own clock.
let frozen: number | null = null;
export const clock = () => frozen ?? Date.now();
/** the browser's first render: read the server's time (called while rendering the root, before any child) */
export function hydrateWith(serverNow: number) { if (typeof window !== "undefined" && !done) frozen = serverNow; }
let done = false;
/** after the hydration commit (the root's effect) */
export function hydrated() { done = true; frozen = null; }
