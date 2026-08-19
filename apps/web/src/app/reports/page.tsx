import { AppNav } from "@/components/app-nav";
import { SellerReports } from "@/components/seller-reports";

export default function ReportsPage() {
  return (
    <main className="min-h-full">
      <AppNav current="reports" />
      <div className="px-4 py-12 sm:px-6 lg:px-8">
        <SellerReports />
      </div>
    </main>
  );
}
