import type { SalesTrafficSyncStatus } from "@/lib/types";

/**
 * 12B.6A — customer-friendly Sales and Traffic synchronization progress
 * language. Never exposes report ids, report document ids, internal
 * leases, or raw worker terminology (queued/started/waiting_to_retry/
 * succeeded/failed/partial/timed_out are this codebase's own internal
 * job-lifecycle vocabulary — customers only ever see the labels below).
 */
export const SALES_TRAFFIC_SYNC_STATUS_LABEL: Record<SalesTrafficSyncStatus, string> = {
  never_synchronized: "Not yet synchronized",
  queued: "Queued for synchronization",
  running: "Importing sales and traffic data",
  waiting_to_retry: "Waiting for Amazon",
  succeeded: "Completed",
  failed: "Needs attention",
  partial: "Needs attention",
  timed_out: "Needs attention",
};

/**
 * Deliberately narrower than Orders' own `ordersSyncShowsActiveSpinner`
 * (which also spins for `queued`): a `queued` Sales and Traffic job has
 * had zero work done on it, and — per this milestone's own known
 * limitations — no worker process is deployed anywhere in production
 * yet, so a queued job may sit unclaimed for an arbitrarily long time.
 * Showing an active spinner for `queued` here would be a literal,
 * indefinite false-progress signal, not a momentary one. Only `running`
 * (a worker has actually claimed the job and is calling Amazon) shows
 * motion; `queued` shows a static "waiting" indicator instead (see the
 * component's own use of this function).
 */
export function salesTrafficSyncShowsActiveSpinner(status: SalesTrafficSyncStatus): boolean {
  return status === "running";
}

export function salesTrafficSyncIsNonTerminal(status: SalesTrafficSyncStatus): boolean {
  return status === "queued" || status === "running" || status === "waiting_to_retry";
}

/**
 * A missing Brand Analytics role surfaces from the backend as a generic
 * `failure_class="authentication_failed"` (SP-API folds 401/403 into one
 * exception type — see the ingestion service's own docstring). No role
 * is tracked ahead of time anywhere in this system yet (an honestly
 * stated gap), so this is the only point where the UI can distinguish
 * "this looks like a permissions problem" from a generic failure, and it
 * does so from the same `failure_class` value every other failure state
 * already carries — never a new API call or a guess.
 */
export function salesTrafficIsLikelyMissingRoleFailure(failureClass: string | null): boolean {
  return failureClass === "authentication_failed";
}

export function formatSalesTrafficFailureReason(failureClass: string | null): string {
  if (salesTrafficIsLikelyMissingRoleFailure(failureClass)) {
    return "This Amazon connection may be missing the Brand Analytics permission. Enable it for this application in Amazon Developer Central, then reconnect Amazon.";
  }
  if (failureClass === "report_cancelled") {
    return "Amazon cancelled this report request (for example, a requested date range that reaches too far into the past).";
  }
  if (failureClass === "report_fatal") {
    return "Amazon could not generate this report.";
  }
  if (failureClass === "malformed_report") {
    return "The report Amazon returned could not be read.";
  }
  return "This synchronization needs attention.";
}

const NUMBER_FORMAT = new Intl.NumberFormat();

export function formatSalesTrafficCount(value: number | null | undefined): string {
  if (value == null) return "—";
  return NUMBER_FORMAT.format(value);
}

export function formatSalesTrafficPercentage(value: string | null | undefined): string {
  if (value == null || value === "") return "—";
  const parsed = Number(value);
  if (Number.isNaN(parsed)) return "—";
  return `${parsed.toFixed(2)}%`;
}

/**
 * A simple, transparent traffic-vs-conversion heuristic for the product
 * table — never a separate endpoint (the read API already returns both
 * traffic and conversion fields on the same row; see
 * `sales_traffic_read.py`'s own docstring). Thresholds are deliberately
 * conservative and documented here rather than tuned against real data,
 * since no live report has ever been ingested by this system.
 */
export const HIGH_TRAFFIC_SESSION_THRESHOLD = 50;
export const LOW_CONVERSION_UNIT_SESSION_PERCENTAGE = 5;

export function isHighTrafficLowConversion(sessions: number | null, unitSessionPercentage: string | null): boolean {
  if (sessions == null || sessions < HIGH_TRAFFIC_SESSION_THRESHOLD) return false;
  if (unitSessionPercentage == null) return false;
  const parsed = Number(unitSessionPercentage);
  return !Number.isNaN(parsed) && parsed < LOW_CONVERSION_UNIT_SESSION_PERCENTAGE;
}
