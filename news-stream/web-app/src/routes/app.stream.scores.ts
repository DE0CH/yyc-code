// /app/stream/scores?date= (approved accounts): each stream item's score and the model's reason that day
import { createFileRoute } from "@tanstack/react-router";
import { approved, json } from "../server/access";
import { db } from "../server/db";
import { streamScores } from "../server/scores";
import { hktToday } from "../server/time";

export const Route = createFileRoute("/app/stream/scores")({
  server: { handlers: { GET: approved(async (_req, _email, url) => {
    const d = url.searchParams.get("date") || hktToday();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return json(400, { error: "date must be YYYY-MM-DD" });
    return json(200, { scores: await streamScores(await db(), d) });
  }) } },
});
