import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { SellerLocalNav } from "@/components/seller-local-nav";
import { SellerInventory } from "@/components/seller-inventory";

export default function SellerInventoryPage() {
  return (
    <AppShell current="seller">
      <Suspense fallback={null}>
        <SellerLocalNav active="inventory" />
      </Suspense>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading FBA Inventory…</p>}>
        <SellerInventory />
      </Suspense>
    </AppShell>
  );
}
