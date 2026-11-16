// /app/wisers/view/<file>: one saved Wisers response, shown without a new search (-wisers/Log.tsx)
import { createFileRoute } from "@tanstack/react-router";
import { WisersView } from "./-wisers/Log";
import { head } from "./-wisers/ui";

export const Route = createFileRoute("/app/wisers/view/$name")({ head: head("已儲存的回應"), component: WisersView });
