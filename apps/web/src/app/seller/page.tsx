import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { SellerLocalNav } from "@/components/seller-local-nav";
import { SellerOverview } from "@/components/seller-overview";

export default function SellerHubPage() {
  return (
    <AppShell current="seller">
      <Suspense fallback={null}>
        <SellerLocalNav active="overview" />
      </Suspense>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading Seller Overview…</p>}>
        <SellerOverview />
      </Suspense>
    </AppShell>
  );
}
