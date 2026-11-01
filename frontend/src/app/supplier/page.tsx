import type { Metadata } from "next";

import SupplierApp from "@/components/SupplierApp";

export const metadata: Metadata = { title: "Supplier · SafePassage" };

export default function Page() {
  return <SupplierApp />;
}
