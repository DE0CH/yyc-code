// /app/wisers/print?job=&ids=: the selected documents laid out for printing (-wisers/Reader.tsx; local, no Wisers print)
import { createFileRoute } from "@tanstack/react-router";
import { WisersPrint } from "./-wisers/Reader";
import { head } from "./-wisers/ui";

const s = (v: unknown) => (typeof v === "string" && v ? v : undefined);
export const Route = createFileRoute("/app/wisers/print")({
  validateSearch: (x: Record<string, unknown>): { job?: string; ids?: string } => ({ job: s(x.job), ids: s(x.ids) }), head: head("列印"), component: WisersPrint });
