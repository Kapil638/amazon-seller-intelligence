import type { ReactNode } from "react";
import Link from "next/link";
import { BarChart3, FileSpreadsheet, History, Search, Sparkles } from "lucide-react";

import { ThemeToggle } from "@/components/theme-toggle";
import { UsagePanel } from "@/components/usage-panel";
import { cn } from "@/lib/utils";

const LINKS = [
  { id: "asin", href: "/", label: "Analyze", icon: Search },
  { id: "copilot", href: "/copilot", label: "Copilot", icon: Sparkles },
  { id: "history", href: "/history", label: "History", icon: History },
  { id: "reports", href: "/reports", label: "Seller Reports", icon: BarChart3 },
  { id: "bulk", href: "/bulk", label: "Bulk Due Diligence", icon: FileSpreadsheet },
] as const;

export function AppShell({
  current,
  children,
}: {
  current: "asin" | "copilot" | "history" | "reports" | "bulk";
  children: ReactNode;
}) {
  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-40 border-b border-border bg-surface/90 backdrop-blur-sm">
        <div className="mx-auto flex h-14 w-full max-w-[1280px] items-center gap-6 px-4 sm:px-6">
          <Link href="/" className="flex shrink-0 items-center gap-2.5">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-[11px] font-semibold tracking-wide text-primary-foreground">
              ASI
            </span>
            <span className="hidden leading-tight sm:block">
              <span className="block text-sm font-semibold tracking-tight">Amazon Seller Intelligence</span>
              <span className="block text-[11px] text-muted-foreground">Commerce Intelligence</span>
            </span>
          </Link>
          <nav className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
            {LINKS.map(({ id, href, label, icon: Icon }) => (
              <Link
                key={id}
                href={href}
                className={cn(
                  "inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 py-3 text-sm transition-colors duration-200",
                  current === id
                    ? "border-primary font-medium text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-1">
            <UsagePanel />
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1280px] px-4 py-8 sm:px-6">{children}</main>
    </div>
  );
}
