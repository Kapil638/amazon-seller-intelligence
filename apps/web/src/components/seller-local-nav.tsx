"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { cn } from "@/lib/utils";

const TABS = [
  { href: "/seller", label: "Overview" },
  { href: "/seller/listings", label: "Listings" },
  { href: "/seller/orders", label: "Orders" },
  { href: "/seller/sales-traffic", label: "Sales & Traffic" },
  // 12B.6B: labeled "FBA Inventory", never bare "Inventory" — this data
  // has no visibility into merchant-fulfilled stock (see
  // seller-inventory-view.ts's own note).
  { href: "/seller/inventory", label: "FBA Inventory" },
] as const;

/**
 * Page-local navigation inside the Seller Hub (12B.4D Phase 7: Orders is
 * deliberately not a global header tab — it lives here instead; 12B.6A
 * adds Sales & Traffic the same way, and 12B.6B adds FBA Inventory the
 * same way too, never as a new top-level tab). Preserves the selected
 * marketplace participation across tabs so switching between them keeps
 * the same marketplace in view rather than resetting to the default.
 */
export function SellerLocalNav({
  active,
}: {
  active: "overview" | "listings" | "orders" | "sales-traffic" | "inventory";
}) {
  const searchParams = useSearchParams();
  const participation = searchParams.get("participation");

  return (
    <nav className="mb-6 flex items-center gap-1 border-b border-border" aria-label="Seller sections">
      {TABS.map((tab) => {
        const isActive =
          (active === "overview" && tab.href === "/seller") ||
          (active === "listings" && tab.href === "/seller/listings") ||
          (active === "orders" && tab.href === "/seller/orders") ||
          (active === "sales-traffic" && tab.href === "/seller/sales-traffic") ||
          (active === "inventory" && tab.href === "/seller/inventory");
        const href = participation ? `${tab.href}?participation=${encodeURIComponent(participation)}` : tab.href;
        return (
          <Link
            key={tab.href}
            href={href}
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "border-b-2 px-3 py-2.5 text-sm transition-colors",
              isActive
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
