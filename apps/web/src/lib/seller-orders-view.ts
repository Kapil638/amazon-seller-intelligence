import type { OrderFulfillmentStatus, OrdersSyncStatus } from "@/lib/types";

/**
 * 12B.4D — customer-friendly Orders synchronization progress language.
 * Never exposes page tokens, internal leases, or raw worker terminology
 * (queued/started/waiting_to_retry/succeeded/failed/partial/timed_out are
 * this codebase's own internal job-lifecycle vocabulary — customers only
 * ever see the labels below).
 */
export const ORDERS_SYNC_STATUS_LABEL: Record<OrdersSyncStatus, string> = {
  never_synchronized: "Not yet synchronized",
  queued: "Queued",
  running: "Importing orders",
  waiting_to_retry: "Waiting for Amazon",
  succeeded: "Completed",
  failed: "Needs attention",
  partial: "Needs attention",
  timed_out: "Needs attention",
};

export const ORDERS_NONTERMINAL_SYNC_STATUSES: OrdersSyncStatus[] = ["queued", "running", "waiting_to_retry"];

export function ordersSyncShowsActiveSpinner(status: OrdersSyncStatus): boolean {
  return status === "queued" || status === "running";
}

export function formatOrdersImportedCount(accepted: number | null | undefined): string {
  const count = accepted ?? 0;
  return `${count.toLocaleString()} order${count === 1 ? "" : "s"} imported`;
}

/**
 * A long-running Orders backfill (12B.4A: the documented ~0.0056 req/s
 * budget can make a full sync span hours) surfaces already-committed
 * pages immediately, while the run is still in progress — this is the
 * customer-facing signal for that partial, still-growing state.
 */
export function ordersMoreHistoryRemains(status: OrdersSyncStatus, paginationComplete: boolean | null): boolean {
  return ORDERS_NONTERMINAL_SYNC_STATUSES.includes(status) && paginationComplete === false;
}

const FULFILLMENT_STATUS_LABEL: Record<OrderFulfillmentStatus, string> = {
  PENDING_AVAILABILITY: "Pending availability",
  PENDING: "Pending",
  UNSHIPPED: "Unshipped",
  PARTIALLY_SHIPPED: "Partially shipped",
  SHIPPED: "Shipped",
  CANCELLED: "Cancelled",
  UNFULFILLABLE: "Unfulfillable",
};

export function formatFulfillmentStatus(status: OrderFulfillmentStatus | null): string {
  if (!status) return "—";
  return FULFILLMENT_STATUS_LABEL[status] ?? status;
}
