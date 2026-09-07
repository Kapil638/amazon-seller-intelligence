import type { InventorySyncStatus } from "@/lib/types";

/**
 * 12B.6B — customer-friendly FBA Inventory synchronization progress
 * language. Never exposes internal leases or raw worker terminology
 * (queued/started/waiting_to_retry/succeeded/failed/timed_out are this
 * codebase's own internal job-lifecycle vocabulary — customers only ever
 * see the labels below). Deliberately has no "partial" entry: this
 * domain can never actually produce that status (see types.ts).
 */
export const INVENTORY_SYNC_STATUS_LABEL: Record<InventorySyncStatus, string> = {
  never_synchronized: "Not yet synchronized",
  queued: "Queued for synchronization",
  running: "Importing FBA inventory",
  waiting_to_retry: "Waiting for Amazon",
  succeeded: "Completed",
  failed: "Needs attention",
  timed_out: "Needs attention",
};

/**
 * Mirrors `salesTrafficSyncShowsActiveSpinner`'s own reasoning: no
 * worker process is deployed anywhere in production yet, so a "queued"
 * job may sit unclaimed for an arbitrarily long time — showing an active
 * spinner for it would be a literal, indefinite false-progress signal.
 * Only "running" (a worker has actually claimed the job and is calling
 * Amazon) shows motion.
 */
export function inventorySyncShowsActiveSpinner(status: InventorySyncStatus): boolean {
  return status === "running";
}

export function inventorySyncIsNonTerminal(status: InventorySyncStatus): boolean {
  return status === "queued" || status === "running" || status === "waiting_to_retry";
}

export function formatInventoryFailureReason(failureClass: string | null): string {
  if (failureClass === "authentication_failed") {
    return "This Amazon connection may need to be reauthorized. Reconnect Amazon and try again.";
  }
  if (failureClass === "pagination_bound_exceeded") {
    return "This catalog has more inventory pages than this system currently allows — contact support.";
  }
  if (failureClass === "invalid_request") {
    return "Amazon rejected this inventory request.";
  }
  return "This synchronization needs attention.";
}

const NUMBER_FORMAT = new Intl.NumberFormat();

export function formatInventoryQuantity(value: number | null | undefined): string {
  if (value == null) return "—";
  return NUMBER_FORMAT.format(value);
}
