import { AppShell } from "@/components/app-shell";
import { ProductLookup } from "@/components/product-lookup";

export default function Home() {
  return (
    <AppShell current="asin">
      <ProductLookup />
    </AppShell>
  );
}
