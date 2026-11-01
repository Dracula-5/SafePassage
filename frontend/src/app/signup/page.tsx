import type { Metadata } from "next";
import { Suspense } from "react";

import { SignUpForm } from "@/components/AuthForms";

export const metadata: Metadata = { title: "Create account · SafePassage" };

export default function Page() {
  return (
    <Suspense>
      <SignUpForm />
    </Suspense>
  );
}
