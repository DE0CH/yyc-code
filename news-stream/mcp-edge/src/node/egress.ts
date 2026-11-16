// The SSRF guard (in-cluster redesign, item 25). An OAuth client may name any https URL as its client id (CIMD), and
// the server fetches it: on Workers, global_fetch_strictly_public kept those fetches on the public internet. Here every
// fetch through the global dispatcher may only connect to a public address: the name is resolved, private, loopback,
// link-local, CGNAT, the pod and Service networks and the like are refused, and the connection goes to the address that
// was checked (no second lookup to rebind). A literal IP in the URL is checked before the fetch. The second layer is
// NetworkPolicy yyc-mcp-egress. The WiseSearch relay, the one in-cluster callee, is reached through `internal`.
// EGRESS_ALLOW (tests only: the fake Access OIDC app on 127.0.0.1) lists hosts the guard lets through; production sets none.
import dns from "node:dns";
import net from "node:net";
import { Agent, setGlobalDispatcher } from "undici";

const blocked = new net.BlockList();
for (const [a, n] of [["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10], ["127.0.0.0", 8], ["169.254.0.0", 16],
  ["172.16.0.0", 12], ["192.0.0.0", 24], ["192.0.2.0", 24], ["192.168.0.0", 16], ["198.18.0.0", 15],
  ["198.51.100.0", 24], ["203.0.113.0", 24], ["224.0.0.0", 4], ["240.0.0.0", 4]] as const) blocked.addSubnet(a, n, "ipv4");
for (const [a, n] of [["::", 128], ["::1", 128], ["fc00::", 7], ["fe80::", 10], ["ff00::", 8], ["64:ff9b::", 96],
  ["2001:db8::", 32], ["100::", 64]] as const) blocked.addSubnet(a, n, "ipv6");

const allow = new Set((process.env.EGRESS_ALLOW || "").split(",").map(s => s.trim()).filter(Boolean));

export function isPublic(ip: string): boolean {
  if (allow.has(ip)) return true;
  const v = net.isIP(ip);
  if (!v) return false;
  const m = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/i.exec(ip);  // IPv4-mapped IPv6: judge the IPv4 address
  if (m) return !blocked.check(m[1], "ipv4");
  return !blocked.check(ip, v === 4 ? "ipv4" : "ipv6");
}

type Addr = { address: string; family: number };
function lookup(host: string, opts: dns.LookupOptions, cb: (e: Error | null, a: string | Addr[], f?: number) => void) {
  dns.lookup(host, { ...opts, all: true }, (err, addrs) => {
    if (err) return cb(err, "");
    const ok = (addrs as Addr[]).filter(a => allow.has(host) || isPublic(a.address));
    if (!ok.length) return cb(Object.assign(new Error(`${host} resolves to no public address`), { code: "EADDRNOTPUBLIC" }), "");
    if (opts.all) cb(null, ok);
    else cb(null, ok[0].address, ok[0].family);
  });
}

/** for the in-cluster relay only */
export const internal = new Agent();

export function guardEgress() {
  setGlobalDispatcher(new Agent({ connect: { lookup: lookup as never } }));
  const f = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const host = new URL(input instanceof Request ? input.url : String(input)).hostname.replace(/^\[|\]$/g, "");
    if (net.isIP(host) && !isPublic(host)) throw new TypeError(`fetch to a non-public address refused: ${host}`);
    return f(input, init);
  }) as typeof fetch;
}
