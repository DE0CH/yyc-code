// /app/wisers/doc/<docId>?job=&i=&style=: one document (-wisers/Reader.tsx): YYC's corpus first, else Wisers (logged)
import { createFileRoute } from "@tanstack/react-router";
import { WisersDoc } from "./-wisers/Reader";
import { head } from "./-wisers/ui";

const s = (v: unknown) => (typeof v === "string" && v ? v : undefined);
export const Route = createFileRoute("/app/wisers/doc/$id")({
  validateSearch: (x: Record<string, unknown>): { job?: string; i?: string; style?: string } => ({ job: s(x.job), i: s(x.i), style: s(x.style) }), head: head("文章"), component: WisersDoc });
