// /app/wisers/media/<id>: a publication's profile from WisersOne (-wisers/Log.tsx; getPublication)
import { createFileRoute } from "@tanstack/react-router";
import { WisersMedia } from "./-wisers/Log";
import { head } from "./-wisers/ui";

export const Route = createFileRoute("/app/wisers/media/$id")({ head: head("媒體資料"), component: WisersMedia });
