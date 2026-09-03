"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { BarChart3, Boxes, Cable, History, MoreHorizontal, Search, Sparkles, X } from "lucide-react";

import { ConnectionHealthDot } from "@/components/connection-health-dot";
import { ThemeToggle } from "@/components/theme-toggle";
import { UsagePanel } from "@/components/usage-panel";
import { cn } from "@/lib/utils";

/**
 * 12B.4D navigation redesign: five primary destinations, replacing the
 * earlier flat, horizontally-scrolling eight-item bar. Existing page
 * functionality is unchanged — this only regroups how it is reached.
 *
 * - Analyze: ASIN analysis (`/`) + Bulk Due Diligence (`/bulk`, reachable
 *   as a secondary link, not a separate primary tab).
 * - Copilot: unchanged (`/copilot`).
 * - Seller: the new Seller Hub (`/seller`), which owns Overview/Listings/
 *   Orders via its own page-local navigation — Orders is deliberately
 *   NOT a global header tab.
 * - Analytics: Profit (`/profit`) + Seller Reports (`/reports`).
 * - Activity: History (`/history`).
 *
 * Connection moved out of primary navigation entirely into the
 * account/settings menu (desktop: inline compact menu; mobile: inside
 * "More"), with a live health-status dot — see `ConnectionHealthDot`.
 */
export type PrimaryDestination = "analyze" | "copilot" | "seller" | "analytics" | "activity";

const PRIMARY_LINKS: {
  id: PrimaryDestination;
  href: string;
  label: string;
  icon: typeof Search;
}[] = [
  { id: "analyze", href: "/", label: "Analyze", icon: Search },
  { id: "copilot", href: "/copilot", label: "Copilot", icon: Sparkles },
  { id: "seller", href: "/seller", label: "Seller", icon: Boxes },
  { id: "analytics", href: "/profit", label: "Analytics", icon: BarChart3 },
  { id: "activity", href: "/history", label: "Activity", icon: History },
];

const ANALYZE_SECONDARY = [{ href: "/bulk", label: "Bulk Due Diligence" }];
const ANALYTICS_SECONDARY = [
  { href: "/profit", label: "Profit" },
  { href: "/reports", label: "Seller Reports" },
];

function SecondaryLinks({ current }: { current: PrimaryDestination }) {
  if (current !== "analyze" && current !== "analytics") return null;
  const items = current === "analyze" ? ANALYZE_SECONDARY : ANALYTICS_SECONDARY;
  return (
    <div className="hidden items-center gap-3 border-l border-border/70 pl-3 text-xs text-muted-foreground xl:flex">
      {items.map((item) => (
        <Link key={item.href} href={item.href} className="whitespace-nowrap hover:text-foreground">
          {item.label}
        </Link>
      ))}
    </div>
  );
}

function AccountMenu() {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="true"
        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-surface-muted hover:text-foreground"
        aria-label="Account and settings"
      >
        <span className="relative">
          <Cable className="h-4 w-4" />
          <ConnectionHealthDot className="absolute -right-0.5 -top-0.5 ring-2 ring-surface" />
        </span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-50 mt-2 w-56 rounded-md border border-border bg-surface p-1.5 shadow-lg"
        >
          <Link
            href="/connection"
            onClick={() => setOpen(false)}
            className="flex items-center justify-between rounded-sm px-2.5 py-2 text-sm hover:bg-surface-muted"
          >
            <span>Amazon Connection</span>
            <ConnectionHealthDot />
          </Link>
        </div>
      )}
    </div>
  );
}

function MobileMoreSheet({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 md:hidden">
      <button
        type="button"
        aria-label="Close menu"
        className="absolute inset-0 bg-black/40"
        onClick={onClose}
      />
      <div className="absolute inset-x-0 bottom-0 rounded-t-xl border-t border-border bg-surface p-4 pb-[calc(env(safe-area-inset-bottom)+1rem)] shadow-2xl">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-sm font-semibold">More</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-surface-muted"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex flex-col gap-1">
          <Link
            href="/history"
            onClick={onClose}
            className="flex items-center gap-2.5 rounded-md px-3 py-2.5 text-sm hover:bg-surface-muted"
          >
            <History className="h-4 w-4" /> Activity
          </Link>
          <Link
            href="/connection"
            onClick={onClose}
            className="flex items-center gap-2.5 rounded-md px-3 py-2.5 text-sm hover:bg-surface-muted"
          >
            <Cable className="h-4 w-4" /> Amazon Connection
            <ConnectionHealthDot className="ml-auto" />
          </Link>
          <div className="flex items-center justify-between rounded-md px-3 py-2.5 text-sm">
            <span className="text-muted-foreground">Usage</span>
            <UsagePanel />
          </div>
          <div className="flex items-center justify-between rounded-md px-3 py-2.5 text-sm">
            <span className="text-muted-foreground">Theme</span>
            <ThemeToggle />
          </div>
        </div>
      </div>
    </div>
  );
}

export function AppShell({
  current,
  children,
}: {
  current: PrimaryDestination;
  children: ReactNode;
}) {
  const [moreOpen, setMoreOpen] = useState(false);

  return (
    <div className="min-h-full pb-16 md:pb-0">
      <header className="sticky top-0 z-40 border-b border-border bg-surface/90 backdrop-blur-sm">
        <div className="mx-auto flex h-14 w-full max-w-[1280px] items-center gap-4 px-4 sm:px-6">
          <Link href="/" className="flex shrink-0 items-center gap-2.5">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-[11px] font-semibold tracking-wide text-primary-foreground">
              ASI
            </span>
            <span className="hidden leading-tight sm:block">
              <span className="block text-sm font-semibold tracking-tight">Amazon Seller Intelligence</span>
              <span className="block text-[11px] text-muted-foreground">Commerce Intelligence</span>
            </span>
          </Link>
          {/* Desktop primary nav: exactly five destinations, no
              horizontal scroll needed at any supported breakpoint.
              Compact-header treatment (768px-1279px, covering the
              1024px "compact laptop" case specifically): labels hide
              and only icons render, each carrying `title`/`aria-label`
              so the destination stays identifiable and reachable, never
              text shrunk to an unreadable size. Full icon+label returns
              at `xl:` (1280px+) — 1440px already comfortably clears
              that. This keeps the nav usable continuously across the
              whole tablet-to-desktop range, not just at two tested
              widths. */}
          <nav aria-label="Primary" className="hidden min-w-0 flex-1 items-center gap-1 md:flex">
            {PRIMARY_LINKS.map(({ id, href, label, icon: Icon }) => (
              <Link
                key={id}
                href={href}
                aria-current={current === id ? "page" : undefined}
                aria-label={label}
                title={label}
                className={cn(
                  "inline-flex items-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 py-3 text-sm transition-colors duration-200",
                  current === id
                    ? "border-primary font-medium text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="h-3.5 w-3.5 shrink-0" />
                <span className="hidden xl:inline">{label}</span>
              </Link>
            ))}
            <SecondaryLinks current={current} />
          </nav>
          <div className="ml-auto flex items-center gap-1">
            {/* Compact utility/account area — separate from primary nav. */}
            <div className="hidden items-center gap-1 md:flex">
              <UsagePanel />
              <ThemeToggle />
              <AccountMenu />
            </div>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1280px] px-4 py-8 sm:px-6">{children}</main>

      {/* Mobile bottom navigation: Analyze/Copilot/Seller/Analytics/More.
          Activity, Connection, Usage, and theme live inside More — see
          MobileMoreSheet. Fixed, never horizontally scrolling. */}
      <nav
        aria-label="Primary mobile"
        className="fixed inset-x-0 bottom-0 z-40 flex h-16 items-stretch border-t border-border bg-surface/95 backdrop-blur-sm md:hidden"
      >
        {PRIMARY_LINKS.slice(0, 4).map(({ id, href, label, icon: Icon }) => (
          <Link
            key={id}
            href={href}
            aria-current={current === id ? "page" : undefined}
            className={cn(
              "flex flex-1 flex-col items-center justify-center gap-1 text-[11px]",
              current === id ? "text-primary" : "text-muted-foreground",
            )}
          >
            <Icon className="h-5 w-5" />
            {label}
          </Link>
        ))}
        <button
          type="button"
          onClick={() => setMoreOpen(true)}
          aria-haspopup="true"
          aria-expanded={moreOpen}
          className={cn(
            "flex flex-1 flex-col items-center justify-center gap-1 text-[11px]",
            current === "activity" ? "text-primary" : "text-muted-foreground",
          )}
        >
          <MoreHorizontal className="h-5 w-5" />
          More
        </button>
      </nav>
      {moreOpen && <MobileMoreSheet onClose={() => setMoreOpen(false)} />}
    </div>
  );
}
