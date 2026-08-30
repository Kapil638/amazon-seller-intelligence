import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { SellerListings } from "@/components/seller-listings";

export default function SellerListingsPage() {
  return (
    <AppShell current="seller-listings">
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading Seller Data…</p>}>
        <SellerListings />
      </Suspense>
    </AppShell>
  );
}
