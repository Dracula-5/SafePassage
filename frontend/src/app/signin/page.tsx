import type { Metadata } from "next";
import { Suspense } from "react";

import { SignInForm } from "@/components/AuthForms";

export const metadata: Metadata = { title: "Sign in · SafePassage" };

export default function Page() {
  return (
    <Suspense>
      <SignInForm />
    </Suspense>
  );
}
