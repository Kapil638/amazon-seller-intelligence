import { AppShell } from "@/components/app-shell";
import { SellerReports } from "@/components/seller-reports";

export default function ReportsPage() {
  return (
    <AppShell current="analytics">
      <SellerReports />
    </AppShell>
  );
}
