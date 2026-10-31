import type { Metadata } from "next";

import SupplierApp from "@/components/SupplierApp";

export const metadata: Metadata = { title: "Supplier · ReliefRoute" };

export default function Page() {
  return <SupplierApp />;
}
