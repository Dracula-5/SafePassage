import type { Metadata } from "next";

import Dashboard from "@/components/Dashboard";

export const metadata: Metadata = { title: "Operations · ReliefRoute" };

export default function Page() {
  return <Dashboard />;
}
