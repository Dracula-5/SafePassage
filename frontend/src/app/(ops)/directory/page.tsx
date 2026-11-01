import type { Metadata } from "next";

import DirectoryPage from "@/components/ops/DirectoryPage";

export const metadata: Metadata = { title: "Directory · SafePassage" };

// The frame waits for the signed-in account before it draws a page, so there is nothing to prerender for an instant navigation.
export const instant = false;

export default function Page() {
  return <DirectoryPage />;
}
