import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { SellerLocalNav } from "@/components/seller-local-nav";
import { SellerSalesTraffic } from "@/components/seller-sales-traffic";

export default function SellerSalesTrafficPage() {
  return (
    <AppShell current="seller">
      <Suspense fallback={null}>
        <SellerLocalNav active="sales-traffic" />
      </Suspense>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading Sales & Traffic…</p>}>
        <SellerSalesTraffic />
      </Suspense>
    </AppShell>
  );
}
