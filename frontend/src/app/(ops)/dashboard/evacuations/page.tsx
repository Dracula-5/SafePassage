import type { Metadata } from "next";

import EvacuationsPage from "@/components/ops/EvacuationsPage";

export const metadata: Metadata = { title: "Evacuations · SafePassage" };

// The frame waits for the signed-in account before it draws a page, so there is nothing to prerender for an instant navigation.
export const instant = false;

export default function Page() {
  return <EvacuationsPage />;
}
