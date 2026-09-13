import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { SellerLocalNav } from "@/components/seller-local-nav";
import { SellerAdvertising } from "@/components/seller-advertising";

export default function SellerAdvertisingPage() {
  return (
    <AppShell current="seller">
      <Suspense fallback={null}>
        <SellerLocalNav active="advertising" />
      </Suspense>
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading Advertising…</p>}>
        <SellerAdvertising />
      </Suspense>
    </AppShell>
  );
}
