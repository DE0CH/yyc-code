// /app/wisers/: the WiseSearch form (-wisers/Form.tsx). server/gates.ts lets in only the WiseSearch Access app's tokens.
import { createFileRoute } from "@tanstack/react-router";
import { validate } from "../lib/wisers";
import { WisersForm } from "./-wisers/Form";
import { head } from "./-wisers/ui";

export const Route = createFileRoute("/app/wisers/")({ validateSearch: validate, head: head("搜尋"), component: WisersForm });
