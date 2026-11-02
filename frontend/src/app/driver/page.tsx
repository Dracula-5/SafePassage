import type { Metadata, Viewport } from "next";

import DriverApp from "@/components/DriverApp";

export const metadata: Metadata = { title: "Driver · ReliefRoute", manifest: "/driver.webmanifest" };
export const viewport: Viewport = { themeColor: "#0f2a43" };

export default function Page() {
  return <DriverApp />;
}
