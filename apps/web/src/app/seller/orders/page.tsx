import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { SellerLocalNav } from "@/components/seller-local-nav";
import { SellerOrders } from "@/components/seller-orders";

export default function SellerOrdersPage() {
  return (
    <AppShell current="seller">
      <Suspense fallback={null}>
        <SellerLocalNav active="orders" />
      </Suspense>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading Orders…</p>}>
        <SellerOrders />
      </Suspense>
    </AppShell>
  );
}
