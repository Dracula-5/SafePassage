import type { Metadata } from "next";

import Directory from "@/components/Directory";

export const metadata: Metadata = { title: "Directory · ReliefRoute" };

export default function Page() {
  return <Directory />;
}
