import type { Metadata } from "next";

import ActivityPage from "@/components/ops/ActivityPage";

export const metadata: Metadata = { title: "Activity & AI · SafePassage" };

// The frame waits for the signed-in account before it draws a page, so there is nothing to prerender for an instant navigation.
export const instant = false;

export default function Page() {
  return <ActivityPage />;
}
