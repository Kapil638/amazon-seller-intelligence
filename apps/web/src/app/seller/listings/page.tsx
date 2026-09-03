import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { SellerListings } from "@/components/seller-listings";
import { SellerLocalNav } from "@/components/seller-local-nav";

export default function SellerListingsPage() {
  return (
    <AppShell current="seller">
      <Suspense fallback={null}>
        <SellerLocalNav active="listings" />
      </Suspense>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading Seller Data…</p>}>
        <SellerListings />
      </Suspense>
    </AppShell>
  );
}
