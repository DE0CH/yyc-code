// A link to any page of the site. Sections already in this app are router links (client-side, preloaded on intent);
// the others are still the old server's pages and get a plain link, a full page load (yyc-edge sends them there).
import type { ReactNode } from "react";
import { Link } from "@tanstack/react-router";

/** the paths this app serves; grows phase by phase (news-stream/web-app/README.md) */
export const IN_APP = new Set<string>(["/", "/item", "/dashboard", "/search", "/reports"]);

export const inApp = (to: string) => IN_APP.has(to) || (to.startsWith("/item/") && IN_APP.has("/item"));

export function siteHref(to: string, search?: Record<string, string | number | null | undefined>, hash?: string) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(search || {})) if (v != null && v !== "") q.set(k, String(v));
  const s = q.toString();
  return to + (s ? "?" + s : "") + (hash ? "#" + hash : "");
}

export function SiteLink({ to, search, hash, className, children, title, ...rest }: {
  to: string; search?: Record<string, string | number | null | undefined>; hash?: string; className?: string;
  children: ReactNode; title?: string; "aria-current"?: "page";
}) {
  if (inApp(to)) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return <Link to={to as any} search={search as any} hash={hash} className={className} title={title} {...rest}>{children}</Link>;
  }
  return <a href={siteHref(to, search, hash)} className={className} title={title} {...rest}>{children}</a>;
}
