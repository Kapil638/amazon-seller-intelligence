import type { AmazonSellerMarketplace, ListingIssue, ListingsSyncStatus } from "@/lib/types";

// Amazon's `www.amazon.com` marketplace id — the canonical standard US
// storefront already recognized throughout this product (see CLAUDE.md /
// the 12B.3D-12B.3E backend test suites, which all default to this exact
// id). Used only as the last-resort default when no marketplace has ever
// had a successful Listings synchronization.
export const CANONICAL_MARKETPLACE_ID = "ATVPDKIKX0DER";

export function marketplaceDisplayName(marketplace: AmazonSellerMarketplace): string {
  return marketplace.name || marketplace.domain_name || marketplace.marketplace_id;
}

export function marketplaceSubtitle(marketplace: AmazonSellerMarketplace): string {
  const country = marketplace.country_code ?? "Unknown country";
  return marketplace.domain_name ? `${country} · ${marketplace.domain_name}` : country;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(date);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatPrice(amount: string | null, currency: string | null): string {
  if (amount == null || amount === "") {
    return "—";
  }
  const value = Number(amount);
  if (Number.isNaN(value)) {
    return "—";
  }
  if (currency) {
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(value);
    } catch {
      // Fall through for a currency code Intl doesn't recognize.
    }
  }
  return currency ? `${currency} ${amount}` : amount;
}

// Amazon's product type is always a SCREAMING_SNAKE_CASE enum value (e.g.
// "BLOOD_OXYGEN_MONITOR"). This is a generic, presentation-only
// transformation — never a lookup table of known values — so it renders
// any type Amazon has ever reported or ever introduces in the future
// exactly as readably, without this file needing to be updated for each
// new one. The stored value, API query parameters, and the product-type
// filter's exact-match behavior are all untouched; only display changes.
export function formatProductType(value: string | null | undefined): string {
  if (!value || !value.trim()) {
    return "—";
  }
  return value
    .split("_")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

export const SYNC_STATUS_LABEL: Record<ListingsSyncStatus, string> = {
  never_synchronized: "Not synchronized yet",
  queued: "Queued",
  running: "Synchronizing…",
  waiting_to_retry: "Waiting to retry",
  succeeded: "Completed",
  failed: "Failed",
  partial: "Partial",
  timed_out: "Timed out",
};

// Nonterminal durable-job states — while the latest run is in one of
// these, a synchronization is genuinely in progress (queued, actively
// running, or paused waiting for a scheduled retry) and the frontend
// should keep polling for updates.
export const NONTERMINAL_SYNC_STATUSES: ListingsSyncStatus[] = ["queued", "running", "waiting_to_retry"];

// The Sync button's label must reflect the *true* state of the run, not
// a single generic "Synchronizing…" for every nonterminal status — a
// `queued` job has had no work done on it at all, and a `waiting_to_retry`
// job is deliberately paused, not actively downloading. Only `running`
// (a worker has actually claimed the job) is genuine active work.
export function syncButtonLabel(status: ListingsSyncStatus): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "running":
      return "Synchronizing…";
    case "waiting_to_retry":
      return "Waiting to retry";
    default:
      return "Sync listings";
  }
}

// An active-work spinner implies a worker is genuinely downloading pages
// right now — true only for `running`. Showing it for `queued` (nothing
// has claimed the job yet) or `waiting_to_retry` (deliberately paused)
// would misrepresent what is actually happening.
export function syncShowsActiveSpinner(status: ListingsSyncStatus): boolean {
  return status === "running";
}

export function formatRelativeFutureTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  if (seconds <= 0) return "shortly";
  if (seconds < 60) return `in about ${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `in about ${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.round(minutes / 60);
  return `in about ${hours} hour${hours === 1 ? "" : "s"}`;
}

const SEVERITY_RANK: Record<string, number> = { ERROR: 3, WARNING: 2, INFO: 1 };

export function highestSeverityFirst(issues: ListingIssue[]): ListingIssue[] {
  return [...issues].sort((a, b) => (SEVERITY_RANK[b.severity] ?? 0) - (SEVERITY_RANK[a.severity] ?? 0));
}
