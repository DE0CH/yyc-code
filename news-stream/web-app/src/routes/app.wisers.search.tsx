// /app/wisers/search?q=…: one WiseSearch search, its own URL and history entry (-wisers/Results.tsx; relay query.criteria())
import { createFileRoute } from "@tanstack/react-router";
import { validate } from "../lib/wisers";
import { WisersSearch } from "./-wisers/Results";
import { head } from "./-wisers/ui";

export const Route = createFileRoute("/app/wisers/search")({ validateSearch: validate, head: head("搜尋結果"), component: WisersSearch });
