// Parts of a staff page that only Deyao sees (Deyao, 2026-10-21: "for pages with elements only i can see, put it in a
// box labeled that it's only visible to me"; NAVIGATION.md "Deyao-only parts"). Every such part of a page goes into
// ONE <DeyaoOnly> box: a bordered box labelled "Only visible to you (Deyao)", in English (proper nouns as the sources
// write them), rendered only when the signed-in account (/app/me) is Deyao's, so staff never see it and the server
// render carries it only in a page rendered for him. When a page's Deyao-only part grows too big for one box, it becomes its own page at its
// own URL (as the monitor and the proof view are). The data inside must be safe for any approved account anyway: this
// hides a box, it is not an access check (Deyao-only data is checked on the server, e.g. /app/monitor/data).
import type { ReactNode } from "react";
import { useMe } from "../queries";
import { ADMIN_EMAIL } from "./admin";

export function DeyaoOnly({ children }: { children: ReactNode }) {
  const me = useMe();
  if (me.data !== ADMIN_EMAIL) return null;
  return (
    <section className="deyao-only" lang="en" aria-label="Only visible to you (Deyao)">
      <p className="deyao-only-label">Only visible to you (Deyao)</p>
      {children}
    </section>
  );
}
