import { AppShell } from "@/components/app-shell";
import { AmazonConnection } from "@/components/amazon-connection";

export default function ConnectionPage() {
  return (
    <AppShell current="seller">
      <AmazonConnection />
    </AppShell>
  );
}
