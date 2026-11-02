import type { Metadata } from "next";
import { Suspense } from "react";

import { DriverView } from "@/components/DriverApp";

export const metadata: Metadata = { title: "Driver's screen · ReliefRoute" };

export default function Page() {
  return (
    <Suspense>
      <DriverView />
    </Suspense>
  );
}
