// Parts of a post the pipeline has not produced yet. They are shown as a static line in words saying what is pending
// (Deyao, 2026-10-16: a spinner is only for a fetch that answers within seconds; lib/Wait.tsx), and the page redraws
// itself when they land (useLive). The server says what is queued where it knows (text_due: the full-text job will
// read the page; comments_read: a fetcher reads this post's comments; a clip's queue place; second_due). For a post
// first seen within PENDING_MINUTES an empty value also counts as not produced yet (tagging and scoring run after
// the poll that found the post; a new social post's comments are read at capture); after that, empty means none.
import type { Item } from "../api";
import { clock } from "./clock.ts";  // with its extension: tests load this file in Node

export const PENDING_MINUTES = 30;

const fresh = (D: Item) => clock() - Date.parse(D.seen) < PENDING_MINUTES * 60_000;

export const tagsPending = (D: Item) => D.tags == null || (D.tags.length === 0 && fresh(D));
/** the full-text job will read the publisher's page (never a social post: its collector gave its whole text) */
export const textPending = (D: Item) => !!D.text_due;
export const commentsPending = (D: Item) =>
  D.thread == null || (!D.comment && !!D.comments_read && D.thread.total === 0 && fresh(D));
export const scorePending = (D: Item) => !D.scoring && (D.not_scored == null || D.not_scored === "due") && fresh(D);
/** anything the live updates still wait for (the second scoring and queued clips included) */
export const anyPending = (D: Item) => tagsPending(D) || textPending(D) || commentsPending(D) || scorePending(D) ||
  !!D.scoring?.second_due || (D.media || []).some(m => m.status === "queued") ||
  (D.pictures?.items || []).some(p => p.status === "queued");
