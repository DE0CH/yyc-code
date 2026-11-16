// Work that outlives its response (the audit write, waitUntil): kept here so SIGTERM waits for it before exiting.
const pending = new Set<Promise<unknown>>();

export function track(p: Promise<unknown>) {
  const q = p.catch(e => console.error("background work failed", e)).finally(() => pending.delete(q));
  pending.add(q);
}

export async function drain(ms: number) {
  await Promise.race([Promise.all([...pending]), new Promise(r => setTimeout(r, ms))]);
}
