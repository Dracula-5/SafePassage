import type { Metadata } from "next";
import { Suspense } from "react";

import FleetPage from "@/components/ops/FleetPage";

export const metadata: Metadata = { title: "Fleet & Drivers · SafePassage" };

// The frame waits for the signed-in account before it draws a page, so there is nothing to prerender for an instant navigation.
export const instant = false;

export default function Page() {
  return (
    <Suspense>
      <FleetPage />
    </Suspense>
  );
}
