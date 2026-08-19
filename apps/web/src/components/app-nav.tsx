import Link from "next/link";

import { ApiBudgetDashboard } from "@/components/api-budget-dashboard";
import { cn } from "@/lib/utils";

export function AppNav({ current }: { current: "asin" | "reports" | "bulk" }) {
  return (
    <header className="border-b border-border bg-card/80">
      <nav className="mx-auto flex w-full max-w-6xl items-center gap-2 px-4 py-3 sm:px-6">
        <Link
          href="/"
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium",
            current === "asin"
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          Analyze ASIN
        </Link>
        <Link
          href="/reports"
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium",
            current === "reports"
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          Seller Reports
        </Link>
        <Link
          href="/bulk"
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium",
            current === "bulk"
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          Bulk Due Diligence
        </Link>
      </nav>
      <ApiBudgetDashboard />
    </header>
  );
}
