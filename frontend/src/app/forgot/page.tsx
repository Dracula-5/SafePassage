import type { Metadata } from "next";
import { Suspense } from "react";

import { ForgotForm } from "@/components/AuthForms";

export const metadata: Metadata = { title: "Forgot password · SafePassage" };

export default function Page() {
  return (
    <Suspense>
      <ForgotForm />
    </Suspense>
  );
}
