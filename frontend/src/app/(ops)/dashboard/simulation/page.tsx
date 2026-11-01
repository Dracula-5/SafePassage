import type { Metadata } from "next";

import SimulationPage from "@/components/ops/SimulationPage";

export const metadata: Metadata = { title: "What-If Simulation · SafePassage" };

// The frame waits for the signed-in account before it draws a page, so there is nothing to prerender for an instant navigation.
export const instant = false;

export default function Page() {
  return <SimulationPage />;
}
