// /app/wisers/browse?job=&ids=: the selected documents one after another (-wisers/Reader.tsx)
import { createFileRoute } from "@tanstack/react-router";
import { WisersBrowse } from "./-wisers/Reader";
import { head } from "./-wisers/ui";

const s = (v: unknown) => (typeof v === "string" && v ? v : undefined);
export const Route = createFileRoute("/app/wisers/browse")({
  validateSearch: (x: Record<string, unknown>): { job?: string; ids?: string } => ({ job: s(x.job), ids: s(x.ids) }), head: head("逐篇瀏覽"), component: WisersBrowse });
