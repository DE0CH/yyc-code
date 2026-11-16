// /app/wisers/log: 紀錄, every search and every other call sent to WisersOne (-wisers/Log.tsx; relay.py api/log, api/log/calls)
import { createFileRoute } from "@tanstack/react-router";
import { WisersLog } from "./-wisers/Log";
import { head } from "./-wisers/ui";

export const Route = createFileRoute("/app/wisers/log")({ head: head("紀錄"), component: WisersLog });
