import { AppShell } from "@/components/app-shell";
import { SellerProfit } from "@/components/seller-profit";

export default function ProfitPage() {
  return (
    <AppShell current="profit">
      <SellerProfit />
    </AppShell>
  );
}
