import { AppShell } from "@/components/app-shell";
import { SellerCopilot } from "@/components/seller-copilot";

export default function CopilotPage() {
  return (
    <AppShell current="copilot">
      <SellerCopilot />
    </AppShell>
  );
}
