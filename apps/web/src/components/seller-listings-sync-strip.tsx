import { AlertTriangle, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/layout";
import {
  formatDateTime,
  formatRelativeFutureTime,
  SYNC_STATUS_LABEL,
  syncButtonLabel,
  syncShowsActiveSpinner,
} from "@/lib/seller-listings-view";
import type { ListingsSyncEvidence } from "@/lib/types";

export type SyncActionMessage = { kind: "success" | "info" | "error"; text: string };

export const THROTTLE_MESSAGE = "Amazon asked us to slow down. Synchronization will resume automatically.";

function statusBadgeClassName(status: ListingsSyncEvidence["status"]): string {
  switch (status) {
    case "succeeded":
      return "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400";
    case "queued":
    case "running":
      return "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-400";
    case "waiting_to_retry":
      return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400";
    case "failed":
    case "timed_out":
      return "border-destructive/30 bg-destructive/10 text-destructive";
    case "partial":
      return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400";
    default:
      return "";
  }
}

function messageClassName(kind: SyncActionMessage["kind"]): string {
  if (kind === "success") return "text-emerald-700 dark:text-emerald-400";
  if (kind === "info") return "text-sky-700 dark:text-sky-400";
  return "text-destructive";
}

export function SellerListingsSyncStrip({
  sync,
  onSync,
  triggering,
  disabled,
  canSync,
  syncMessage,
  queuePollingSuspended,
  onRefreshStatus,
}: {
  sync: ListingsSyncEvidence;
  onSync: () => void;
  // True only while the trigger POST itself is in flight — genuine active
  // work distinct from the job's own (possibly nonterminal-but-idle)
  // status, so the button can show a spinner for it regardless of what
  // `sync.status` currently says.
  triggering: boolean;
  // True whenever the button should be disabled: `triggering`, or an
  // active (queued/running/waiting_to_retry) job already exists.
  disabled: boolean;
  canSync: boolean;
  syncMessage: SyncActionMessage | null;
  // True once a `queued` job has sat unclaimed past the stale-queue
  // threshold with no `started_at`/heartbeat/progress — see
  // `seller-listings.tsx`'s polling effect. Switches the queued
  // explanation from "waiting for a worker" to "still queued" and
  // replaces automatic polling with a manual refresh action.
  queuePollingSuspended: boolean;
  onRefreshStatus: () => void;
}) {
  const latestFailed = sync.status === "failed" || sync.status === "timed_out" || sync.status === "partial";
  const hasLastKnownGood = Boolean(sync.last_successful_synchronized_at);
  const waitingToRetry = sync.status === "waiting_to_retry";
  const retryEta = waitingToRetry ? formatRelativeFutureTime(sync.next_retry_at) : null;
  const isQueued = sync.status === "queued";
  const showSpinner = triggering || syncShowsActiveSpinner(sync.status);
  const buttonLabel = triggering ? "Sync listings" : syncButtonLabel(sync.status);

  return (
    <Panel className="p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <Badge variant="outline" className={statusBadgeClassName(sync.status)}>
            {SYNC_STATUS_LABEL[sync.status]}
          </Badge>
          {sync.status !== "never_synchronized" && sync.status !== "queued" ? (
            <span className="text-xs text-muted-foreground">
              {sync.records_accepted ?? 0} accepted
              {sync.records_rejected ? ` · ${sync.records_rejected} rejected` : ""}
              {sync.pages_fetched ? ` · ${sync.pages_fetched} page${sync.pages_fetched === 1 ? "" : "s"}` : ""}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-3">
          <p className="text-xs text-muted-foreground">
            Last successful sync: {formatDateTime(sync.last_successful_synchronized_at)}
          </p>
          <Button type="button" size="sm" disabled={!canSync || disabled} onClick={onSync}>
            {showSpinner ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {buttonLabel}
          </Button>
        </div>
      </div>
      {isQueued && !queuePollingSuspended ? (
        <div className="mt-2 text-xs text-muted-foreground">
          <p className="font-medium text-foreground">Waiting for synchronization worker</p>
          <p>Your existing listing data remains available. You may leave this page and return later.</p>
        </div>
      ) : null}
      {isQueued && queuePollingSuspended ? (
        <div className="mt-2 text-xs text-muted-foreground">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-medium text-foreground">Still queued</p>
            <Button type="button" size="sm" variant="outline" className="h-6 px-2 text-xs" onClick={onRefreshStatus}>
              Refresh status
            </Button>
          </div>
          <p>Processing has not started yet. Your existing listing data remains available.</p>
        </div>
      ) : null}
      {waitingToRetry ? (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {THROTTLE_MESSAGE}
          {retryEta ? ` (retrying ${retryEta})` : ""}
        </p>
      ) : null}
      {latestFailed && hasLastKnownGood ? (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          The latest synchronization attempt did not complete. Listings below are from the last
          successful sync, {formatDateTime(sync.last_successful_synchronized_at)}.
        </p>
      ) : null}
      {latestFailed && !hasLastKnownGood ? (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-destructive">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          No Listings synchronization has ever succeeded for this marketplace yet.
        </p>
      ) : null}
      <p role="status" aria-live="polite" className={`mt-2 text-xs ${syncMessage ? messageClassName(syncMessage.kind) : "sr-only"}`}>
        {syncMessage?.text ?? ""}
      </p>
    </Panel>
  );
}
