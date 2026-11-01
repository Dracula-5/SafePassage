import type { Metadata, Viewport } from "next";

import DriverApp from "@/components/DriverApp";

export const metadata: Metadata = { title: "Driver · SafePassage", manifest: "/driver.webmanifest" };
export const viewport: Viewport = { themeColor: "#ffffff" };

export default function Page() {
  return <DriverApp />;
}
