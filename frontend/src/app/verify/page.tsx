import type { Metadata } from "next";
import { Suspense } from "react";

import { VerifyEmail } from "@/components/AuthForms";

export const metadata: Metadata = { title: "Confirm email · SafePassage" };

export default function Page() {
  return (
    <Suspense>
      <VerifyEmail />
    </Suspense>
  );
}
