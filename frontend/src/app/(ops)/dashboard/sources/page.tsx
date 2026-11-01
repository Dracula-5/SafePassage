import type { Metadata } from "next";

import SourcesPage from "@/components/ops/SourcesPage";

export const metadata: Metadata = { title: "Data Sources · SafePassage" };

// The frame waits for the signed-in account before it draws a page, so there is nothing to prerender for an instant navigation.
export const instant = false;

export default function Page() {
  return <SourcesPage />;
}
