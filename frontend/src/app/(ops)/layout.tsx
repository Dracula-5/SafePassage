import OpsShell from "@/components/ops/OpsShell";

// Every duty-officer page shares one frame, one sign-in check and one set of live data.
export default function OpsLayout({ children }: { children: React.ReactNode }) {
  return <OpsShell>{children}</OpsShell>;
}
