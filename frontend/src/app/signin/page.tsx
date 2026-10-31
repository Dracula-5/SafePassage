import type { Metadata } from "next";
import { Suspense } from "react";

import { SignInForm } from "@/components/AuthForms";

export const metadata: Metadata = { title: "Sign in · ReliefRoute" };

export default function Page() {
  return (
    <Suspense>
      <SignInForm />
    </Suspense>
  );
}
