import type { Metadata } from "next";
import { Suspense } from "react";

import { ResetForm } from "@/components/AuthForms";

export const metadata: Metadata = { title: "New password · SafePassage" };

export default function Page() {
  return (
    <Suspense>
      <ResetForm />
    </Suspense>
  );
}
