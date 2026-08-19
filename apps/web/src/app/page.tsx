import { AppNav } from "@/components/app-nav";
import { ProductLookup } from "@/components/product-lookup";

export default function Home() {
  return (
    <main className="min-h-full">
      <AppNav current="asin" />
      <div className="px-4 py-12 sm:px-6 lg:px-8">
        <ProductLookup />
      </div>
    </main>
  );
}
