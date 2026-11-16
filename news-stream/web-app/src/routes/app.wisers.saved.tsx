// /app/wisers/saved: every saved Wisers response (-wisers/Log.tsx); opening one costs no search
import { createFileRoute } from "@tanstack/react-router";
import { WisersSaved } from "./-wisers/Log";
import { head } from "./-wisers/ui";

export const Route = createFileRoute("/app/wisers/saved")({ head: head("已儲存的回應"), component: WisersSaved });
