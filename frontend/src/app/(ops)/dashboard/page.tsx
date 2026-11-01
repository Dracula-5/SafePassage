import type { Metadata } from "next";

import CommandCenter from "@/components/ops/CommandCenter";

export const metadata: Metadata = { title: "Command Center · SafePassage" };

// The frame waits for the signed-in account before it draws a page, so there is nothing to prerender for an instant navigation.
export const instant = false;

export default function Page() {
  return <CommandCenter />;
}
