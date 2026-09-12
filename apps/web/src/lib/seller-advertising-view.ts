import type { AdsSyncStatus } from "@/lib/types";

// 12C — Amazon Ads read-only foundation. Pure presentation helpers,
// mirroring `seller-sales-traffic-view.ts`'s own separation of view
// logic from the fetching component.

export const ADS_SYNC_STATUS_LABEL: Record<AdsSyncStatus, string> = {
  not_configured: "Not available yet",
  not_connected: "Not connected",
  connected_no_profile: "Select an advertiser profile",
  awaiting_first_sync: "Waiting for first sync",
  synced: "Synced",
  delayed: "Sync delayed",
  failed: "Sync failed",
};

export function formatAdsMoney(value: string | null | undefined, currency: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: currency || "USD" }).format(amount);
  } catch {
    return `${currency ?? ""} ${amount.toFixed(2)}`.trim();
  }
}

export function formatAdsPercent(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  return `${amount.toFixed(2)}%`;
}

export function formatAdsRatio(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  return amount.toFixed(2);
}

export function formatAdsCount(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

function toIsoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function defaultAdsDateRange(days: number, now: Date = new Date()): { start: string; end: string } {
  const end = new Date(now);
  const start = new Date(now);
  start.setDate(start.getDate() - days);
  return { start: toIsoDate(start), end: toIsoDate(end) };
}

export const ADS_PERIOD_OPTIONS = [
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
] as const;
export type AdsPeriodValue = (typeof ADS_PERIOD_OPTIONS)[number]["value"];

export function parseAdsPeriod(value: string | null): AdsPeriodValue {
  return ADS_PERIOD_OPTIONS.some((option) => option.value === value) ? (value as AdsPeriodValue) : "30";
}
