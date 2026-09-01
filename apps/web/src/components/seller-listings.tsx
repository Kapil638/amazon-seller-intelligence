"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { EmptyState, PageHeader } from "@/components/ui/layout";
import {
  AmazonConnectionError,
  fetchAmazonConnection,
  fetchListings,
  fetchListingsSummary,
  ListingsSyncError,
  triggerListingsSync,
} from "@/lib/api";
import { CANONICAL_MARKETPLACE_ID, NONTERMINAL_SYNC_STATUSES, formatDateTime } from "@/lib/seller-listings-view";
import type {
  AmazonSellerMarketplace,
  ListingCollectionResponse,
  ListingsSummary,
  ListingsSyncStatus,
} from "@/lib/types";

// 12B.3G: the trigger enqueues a durable job and returns almost
// immediately — actual progress is observed by polling the summary
// endpoint (which already carries the latest run's sanitized status;
// see `ListingsSyncEvidence`), not by awaiting one long request. Backoff
// keeps this cheap for a job that stays queued/running for a while;
// exported so tests can reason about the exact schedule with fake timers.
export const LISTINGS_SYNC_POLL_INITIAL_MS = 3000;
export const LISTINGS_SYNC_POLL_MAX_MS = 20000;
export const LISTINGS_SYNC_POLL_BACKOFF_FACTOR = 1.5;

// A `queued` job with no `started_at`, no heartbeat, and no progress
// change after this long has almost certainly not been claimed by any
// worker (a live-observed defect: with no worker deployed, a queued job
// otherwise polls — and appears to "synchronize" — forever). Past this
// threshold, automatic polling stops and the UI switches to a manual
// "Refresh status" action rather than silently implying active work that
// is not happening. The durable job itself is completely unaffected —
// this only changes what the browser polls, never the database.
export const LISTINGS_SYNC_STALE_QUEUE_THRESHOLD_MS = 90000;

import {
  DEFAULT_FILTER_STATE,
  SellerListingsFilters,
  type SellerListingsFilterState,
} from "@/components/seller-listings-filters";
import { SellerListingsDetail } from "@/components/seller-listings-detail";
import { SellerListingsMarketplaceSelector } from "@/components/seller-listings-marketplace-selector";
import { SellerListingsSummaryMetrics } from "@/components/seller-listings-summary";
import { SellerListingsSyncStrip, type SyncActionMessage } from "@/components/seller-listings-sync-strip";
import { SellerListingsTable } from "@/components/seller-listings-table";

const PAGE_SIZE = 25;

type AddressableMarketplace = AmazonSellerMarketplace & { id: string };

function parseFilters(params: URLSearchParams): SellerListingsFilterState {
  const active = params.get("active");
  const buyable = params.get("buyable");
  const discoverable = params.get("discoverable");
  const issues = params.get("issues");
  const severity = params.get("severity");
  return {
    q: params.get("q") ?? "",
    isActive: active === "true" ? true : active === "false" ? false : undefined,
    isBuyable: buyable === "true" ? true : buyable === "false" ? false : undefined,
    isDiscoverable: discoverable === "true" ? true : discoverable === "false" ? false : undefined,
    hasIssues: issues === "true" ? true : issues === "false" ? false : undefined,
    highestIssueSeverity:
      severity === "ERROR" || severity === "WARNING" || severity === "INFO" ? severity : undefined,
    productType: params.get("product_type") ?? "",
    sortBy: (params.get("sort") as SellerListingsFilterState["sortBy"]) || DEFAULT_FILTER_STATE.sortBy,
    sortDir: (params.get("dir") as SellerListingsFilterState["sortDir"]) || DEFAULT_FILTER_STATE.sortDir,
  };
}

// Prefer a marketplace with a *successful* Listings synchronization (most
// recent wins if more than one qualifies); otherwise the canonical
// standard storefront already recognized by the product; otherwise the
// first available marketplace. Never proof-by-authorization: a
// participation's mere presence here says nothing about whether Listings
// data exists for it.
function pickDefaultMarketplace(
  marketplaces: AddressableMarketplace[],
  results: PromiseSettledResult<ListingsSummary>[],
): string {
  let bestId: string | undefined;
  let bestSyncedAt: string | undefined;
  results.forEach((result, index) => {
    if (result.status !== "fulfilled") return;
    const evidence = result.value.sync;
    if (evidence.status !== "succeeded" || !evidence.last_successful_synchronized_at) return;
    if (!bestSyncedAt || evidence.last_successful_synchronized_at > bestSyncedAt) {
      bestId = marketplaces[index].id;
      bestSyncedAt = evidence.last_successful_synchronized_at;
    }
  });
  if (bestId) return bestId;
  const canonical = marketplaces.find((m) => m.marketplace_id === CANONICAL_MARKETPLACE_ID);
  if (canonical) return canonical.id;
  return marketplaces[0].id;
}

function parsePage(params: URLSearchParams): number {
  const raw = Number(params.get("page") ?? "1");
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1;
}

export function SellerListings() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [connectionLoading, setConnectionLoading] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [sellerAccountId, setSellerAccountId] = useState<string | null>(null);
  const [marketplaces, setMarketplaces] = useState<AddressableMarketplace[]>([]);

  const [resolvingDefault, setResolvingDefault] = useState(false);

  const [summary, setSummary] = useState<ListingsSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [collection, setCollection] = useState<ListingCollectionResponse | null>(null);
  const [listingsLoading, setListingsLoading] = useState(false);
  const [listingsError, setListingsError] = useState<string | null>(null);

  // True only while the trigger POST itself is in flight — the resulting
  // job may still be `queued`/`started`/`waiting_to_retry` long after this
  // becomes false again. `syncBusy` (derived from `summary.sync.status`
  // below) is what actually drives the button's disabled/spinner state.
  const [triggering, setTriggering] = useState(false);
  const [syncMessage, setSyncMessage] = useState<SyncActionMessage | null>(null);
  // True once a `queued` job has been sitting unclaimed past the
  // stale-queue threshold — see `LISTINGS_SYNC_STALE_QUEUE_THRESHOLD_MS`.
  // Automatic polling stops; only `handleRefreshStatus` fetches after this.
  const [queuePollingSuspended, setQueuePollingSuspended] = useState(false);
  // Bumped when a run transitions into a terminal state, to force the
  // summary/listings/detail effects below to refetch even when none of
  // their own dependencies (participation, filters, page) changed.
  const [refreshToken, setRefreshToken] = useState(0);
  const pollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollDelayRef = useRef(LISTINGS_SYNC_POLL_INITIAL_MS);
  const previousSyncStatusRef = useRef<ListingsSyncStatus | null>(null);

  const participationId = searchParams.get("participation");
  // `handleSync` below closes over whatever `participationId` was current
  // when it was created; if the user switches marketplaces while its POST
  // is still in flight, the promise chain must not apply that stale
  // response (a message, a summary refetch, a pagination reset) to
  // whatever marketplace is now selected. A ref (always current, unlike
  // the closed-over value) lets each in-flight request check whether it
  // is still the one the page is showing before touching state.
  const participationIdRef = useRef(participationId);
  useEffect(() => {
    participationIdRef.current = participationId;
  }, [participationId]);
  const listingId = searchParams.get("listing");
  const filters = useMemo(() => parseFilters(searchParams), [searchParams]);
  const page = parsePage(searchParams);
  const offset = (page - 1) * PAGE_SIZE;

  const replaceParams = useCallback(
    (mutate: (params: URLSearchParams) => void) => {
      const next = new URLSearchParams(searchParams.toString());
      mutate(next);
      const query = next.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  // 1. Load the Connection API's marketplace participations. This is the
  // only source of marketplace_participation_id used anywhere in this
  // component — never hardcoded, never invented.
  useEffect(() => {
    let cancelled = false;
    setConnectionLoading(true);
    setConnectionError(null);
    fetchAmazonConnection()
      .then((overview) => {
        if (cancelled) return;
        setSellerAccountId(overview.seller_account_id ?? null);
        const addressable = (overview.marketplaces ?? []).filter(
          (m): m is AddressableMarketplace => Boolean(m.id),
        );
        setMarketplaces(addressable);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setConnectionError(
          err instanceof AmazonConnectionError
            ? err.message
            : "Amazon Connection could not be reached.",
        );
      })
      .finally(() => {
        if (!cancelled) setConnectionLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 2. Resolve which participation is selected: URL first, else prefer one
  // with a successful Listings sync, else the canonical standard
  // storefront, else the first available.
  useEffect(() => {
    if (connectionLoading || marketplaces.length === 0) {
      return;
    }
    const validSelection = marketplaces.some((m) => m.id === participationId);
    if (validSelection) {
      return;
    }

    let cancelled = false;
    setResolvingDefault(true);
    Promise.allSettled(marketplaces.map((m) => fetchListingsSummary(m.id)))
      .then((results) => {
        if (cancelled) return;
        const chosen = pickDefaultMarketplace(marketplaces, results);
        replaceParams((params) => {
          params.set("participation", chosen);
        });
      })
      .finally(() => {
        if (!cancelled) setResolvingDefault(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectionLoading, marketplaces, participationId]);

  // 3. Summary: only depends on the selected participation.
  useEffect(() => {
    if (!participationId || !marketplaces.some((m) => m.id === participationId)) {
      setSummary(null);
      return;
    }
    let cancelled = false;
    setSummaryLoading(true);
    setSummaryError(null);
    fetchListingsSummary(participationId)
      .then((result) => {
        if (!cancelled) setSummary(result);
      })
      .catch(() => {
        if (!cancelled) setSummaryError("The summary could not be loaded.");
      })
      .finally(() => {
        if (!cancelled) setSummaryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [participationId, marketplaces, refreshToken]);

  // 4. Listings collection: depends on participation + filters + page.
  useEffect(() => {
    if (!participationId || !marketplaces.some((m) => m.id === participationId)) {
      setCollection(null);
      return;
    }
    let cancelled = false;
    setListingsLoading(true);
    setListingsError(null);
    fetchListings(participationId, {
      q: filters.q || undefined,
      isActive: filters.isActive,
      isBuyable: filters.isBuyable,
      isDiscoverable: filters.isDiscoverable,
      hasIssues: filters.hasIssues,
      highestIssueSeverity: filters.highestIssueSeverity,
      productType: filters.productType || undefined,
      sortBy: filters.sortBy,
      sortDir: filters.sortDir,
      offset,
      limit: PAGE_SIZE,
    })
      .then((result) => {
        if (!cancelled) setCollection(result);
      })
      .catch(() => {
        if (!cancelled) setListingsError("Listings could not be loaded. Please try again.");
      })
      .finally(() => {
        if (!cancelled) setListingsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [participationId, marketplaces, filters, offset, refreshToken]);

  // 5. Poll the summary endpoint while the latest run is nonterminal
  // (queued/running/waiting_to_retry) — this is how the page discovers
  // and resumes an active run on reload or one started elsewhere,
  // without needing to persist a run id anywhere client-side. Stops on a
  // terminal transition, on participation change, on unmount, and pauses
  // (without losing its place) while the tab is hidden — resuming
  // immediately, with an out-of-band catch-up fetch, when it becomes
  // visible again.
  const syncStatus = summary?.sync.status ?? null;
  const queuedAt = summary?.sync.queued_at ?? null;

  const isStaleQueue = useCallback((status: ListingsSyncStatus | null, queuedAtValue: string | null): boolean => {
    if (status !== "queued" || !queuedAtValue) return false;
    const queuedAtMs = new Date(queuedAtValue).getTime();
    if (Number.isNaN(queuedAtMs)) return false;
    return Date.now() - queuedAtMs >= LISTINGS_SYNC_STALE_QUEUE_THRESHOLD_MS;
  }, []);

  useEffect(() => {
    const previousStatus = previousSyncStatusRef.current;
    previousSyncStatusRef.current = syncStatus;

    const wasNonterminal = previousStatus !== null && NONTERMINAL_SYNC_STATUSES.includes(previousStatus);
    const isNonterminal = syncStatus !== null && NONTERMINAL_SYNC_STATUSES.includes(syncStatus);

    if (wasNonterminal && !isNonterminal) {
      // A run just finished (successfully or not) — refresh listings/
      // detail once, and reset backoff for the next time a sync starts.
      setRefreshToken((token) => token + 1);
      pollDelayRef.current = LISTINGS_SYNC_POLL_INITIAL_MS;
    }

    if (!isNonterminal || !participationId) {
      if (pollTimeoutRef.current) clearTimeout(pollTimeoutRef.current);
      pollTimeoutRef.current = null;
      setQueuePollingSuspended(false);
      return;
    }

    // Already stale the moment this effect (re)starts — e.g. a page
    // reload discovering a job that has been queued, unclaimed, for a
    // long time already. Show that truthfully immediately; do not wait
    // another full poll interval, and do not schedule automatic polling
    // at all — only `handleRefreshStatus` fetches from here.
    if (isStaleQueue(syncStatus, queuedAt)) {
      setQueuePollingSuspended(true);
      pollDelayRef.current = LISTINGS_SYNC_POLL_INITIAL_MS;
      return;
    }
    setQueuePollingSuspended(false);

    let cancelled = false;
    const pollOnce = async () => {
      try {
        const result = await fetchListingsSummary(participationId);
        if (!cancelled) setSummary(result);
      } catch {
        // A single failed background poll is not worth surfacing as a
        // page-level error — the next tick tries again.
      }
    };
    const scheduleNext = () => {
      if (cancelled) return;
      pollTimeoutRef.current = setTimeout(() => {
        if (cancelled) return;
        // Loss of relevance: don't spend a request on a hidden tab, but
        // keep the schedule alive so polling resumes on its own cadence
        // once the tab is visible again (the visibilitychange listener
        // below also does an immediate catch-up fetch).
        if (typeof document !== "undefined" && document.hidden) {
          scheduleNext();
          return;
        }
        // Stale-queue circuit breaker: this run has sat `queued` with no
        // worker progress past the threshold — stop polling automatically
        // rather than continuing to imply active work forever.
        if (isStaleQueue(syncStatus, queuedAt)) {
          setQueuePollingSuspended(true);
          pollDelayRef.current = LISTINGS_SYNC_POLL_INITIAL_MS;
          return;
        }
        pollDelayRef.current = Math.min(
          pollDelayRef.current * LISTINGS_SYNC_POLL_BACKOFF_FACTOR,
          LISTINGS_SYNC_POLL_MAX_MS,
        );
        pollOnce().finally(scheduleNext);
      }, pollDelayRef.current);
    };
    scheduleNext();

    const handleVisibilityChange = () => {
      if (typeof document !== "undefined" && !document.hidden) {
        pollOnce();
      }
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", handleVisibilityChange);
    }

    return () => {
      cancelled = true;
      if (pollTimeoutRef.current) clearTimeout(pollTimeoutRef.current);
      pollTimeoutRef.current = null;
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", handleVisibilityChange);
      }
    };
  }, [syncStatus, queuedAt, participationId, isStaleQueue]);

  const syncBusy = syncStatus !== null && NONTERMINAL_SYNC_STATUSES.includes(syncStatus);

  // Manual refresh for a stale queued job — a single fetch, never the
  // trigger endpoint, never assumed to change the job's actual status.
  // If the fetched status is no longer `queued` (or no longer stale),
  // the polling effect above resumes its normal behavior on its own,
  // since `syncStatus`/`queuedAt` will have changed.
  const handleRefreshStatus = useCallback(() => {
    if (!participationId) return;
    fetchListingsSummary(participationId)
      .then((result) => setSummary(result))
      .catch(() => {
        // A failed manual refresh leaves the current (already-truthful)
        // state on screen rather than surfacing a page-level error.
      });
  }, [participationId]);

  const handleMarketplaceChange = useCallback(
    (nextId: string) => {
      replaceParams((params) => {
        params.set("participation", nextId);
        params.delete("page");
        params.delete("listing");
      });
    },
    [replaceParams],
  );

  const handleFilterChange = useCallback(
    (patch: Partial<SellerListingsFilterState>) => {
      replaceParams((params) => {
        const next = { ...filters, ...patch };
        if (next.q) params.set("q", next.q);
        else params.delete("q");
        if (next.isActive === undefined) params.delete("active");
        else params.set("active", String(next.isActive));
        if (next.isBuyable === undefined) params.delete("buyable");
        else params.set("buyable", String(next.isBuyable));
        if (next.isDiscoverable === undefined) params.delete("discoverable");
        else params.set("discoverable", String(next.isDiscoverable));
        if (next.hasIssues === undefined) params.delete("issues");
        else params.set("issues", String(next.hasIssues));
        if (next.highestIssueSeverity === undefined) params.delete("severity");
        else params.set("severity", next.highestIssueSeverity);
        if (next.productType) params.set("product_type", next.productType);
        else params.delete("product_type");
        if (next.sortBy === DEFAULT_FILTER_STATE.sortBy) params.delete("sort");
        else params.set("sort", next.sortBy);
        if (next.sortDir === DEFAULT_FILTER_STATE.sortDir) params.delete("dir");
        else params.set("dir", next.sortDir);
        params.delete("page");
      });
    },
    [filters, replaceParams],
  );

  const handleResetFilters = useCallback(() => {
    replaceParams((params) => {
      params.delete("q");
      params.delete("active");
      params.delete("buyable");
      params.delete("discoverable");
      params.delete("issues");
      params.delete("severity");
      params.delete("product_type");
      params.delete("sort");
      params.delete("dir");
      params.delete("page");
    });
  }, [replaceParams]);

  const handlePageChange = useCallback(
    (nextOffset: number) => {
      replaceParams((params) => {
        const nextPage = Math.floor(nextOffset / PAGE_SIZE) + 1;
        if (nextPage <= 1) params.delete("page");
        else params.set("page", String(nextPage));
      });
    },
    [replaceParams],
  );

  const handleOpenDetail = useCallback(
    (id: string) => {
      replaceParams((params) => {
        params.set("listing", id);
      });
    },
    [replaceParams],
  );

  const handleCloseDetail = useCallback(() => {
    replaceParams((params) => {
      params.delete("listing");
    });
  }, [replaceParams]);

  const handleSync = useCallback(() => {
    if (!participationId || triggering || syncBusy) {
      return;
    }
    // Captured once, at the moment this request is fired — never
    // re-read from the (possibly since-changed) `participationId`
    // closure variable while the request is in flight.
    const requestedParticipationId = participationId;
    const isStillCurrent = () => participationIdRef.current === requestedParticipationId;
    setTriggering(true);
    setSyncMessage(null);
    triggerListingsSync(requestedParticipationId)
      .then((response) => {
        if (!isStillCurrent()) {
          // The user switched marketplaces while this POST was in
          // flight — this response no longer describes what's on
          // screen. The newly selected marketplace's own effects
          // already own fetching and displaying its own state.
          return undefined;
        }
        if (response.reason === "queued") {
          // Reset pagination so refreshed results are understandable once
          // the job completes; the polling effect above picks up the new
          // `queued` status (and everything after it) from here.
          replaceParams((params) => {
            params.delete("page");
          });
        } else if (response.reason === "already_running") {
          setSyncMessage({
            kind: "info",
            text: response.message ?? "A Listings synchronization is already running for this marketplace.",
          });
        } else if (response.reason === "cooldown") {
          setSyncMessage({
            kind: "info",
            text: response.retry_allowed_at
              ? `Recently synchronized. You can synchronize again at ${formatDateTime(response.retry_allowed_at)}.`
              : (response.message ?? "Recently synchronized. Please try again shortly."),
          });
        } else {
          setSyncMessage({
            kind: "error",
            text: response.message ?? "Synchronization could not be started.",
          });
        }
        // Whether we just queued a fresh job or discovered an existing
        // one, refetch summary immediately so the strip reflects truthful
        // current state without waiting for the next poll tick.
        return fetchListingsSummary(requestedParticipationId).then((result) => {
          if (isStillCurrent()) setSummary(result);
        });
      })
      .catch((err: unknown) => {
        if (!isStillCurrent()) return;
        setSyncMessage({
          kind: "error",
          text: err instanceof ListingsSyncError ? err.message : "Synchronization could not be started.",
        });
      })
      .finally(() => {
        // Always cleared, regardless of which marketplace is now
        // selected — this is a global "is a sync POST in flight"
        // indicator, and must never leave the (possibly now-different)
        // Sync button permanently disabled.
        setTriggering(false);
      });
  }, [participationId, triggering, syncBusy, replaceParams]);

  if (connectionLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading Seller Data…
      </div>
    );
  }

  if (connectionError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Seller Data unavailable</AlertTitle>
        <AlertDescription>{connectionError}</AlertDescription>
      </Alert>
    );
  }

  if (!sellerAccountId) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Seller Data"
          description="Your own Amazon catalog and Amazon-reported listing health, marketplace by marketplace. Different from ASIN Analyzer, which evaluates public marketplace products."
        />
        <EmptyState
          title="No connected seller account yet."
          description="Connect Amazon and complete seller validation on the Connection page before Seller Data is available."
        />
      </div>
    );
  }

  if (marketplaces.length === 0) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Seller Data"
          description="Your own Amazon catalog and Amazon-reported listing health, marketplace by marketplace."
        />
        <EmptyState
          title="No marketplaces available yet."
          description="Being authorized with Amazon does not by itself mean marketplace or Listings data exists. Validate your connection on the Connection page."
        />
      </div>
    );
  }

  const selected = marketplaces.find((m) => m.id === participationId) ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Seller Data"
        description="Your own Amazon catalog and Amazon-reported listing health for the selected marketplace — different from ASIN Analyzer, which evaluates public marketplace products."
      >
        <SellerListingsMarketplaceSelector
          marketplaces={marketplaces}
          selectedId={participationId}
          onChange={handleMarketplaceChange}
          disabled={resolvingDefault}
        />
      </PageHeader>

      {resolvingDefault || !selected ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Choosing a marketplace…
        </div>
      ) : (
        <>
          {summaryError ? (
            <Alert variant="destructive">
              <AlertTitle>Summary unavailable</AlertTitle>
              <AlertDescription>{summaryError}</AlertDescription>
            </Alert>
          ) : null}

          {summaryLoading && !summary ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading summary…
            </div>
          ) : null}

          {summary ? (
            <>
              <SellerListingsSyncStrip
                sync={summary.sync}
                onSync={handleSync}
                triggering={triggering}
                disabled={triggering || syncBusy}
                canSync={Boolean(participationId) && !resolvingDefault}
                syncMessage={syncMessage}
                queuePollingSuspended={queuePollingSuspended}
                onRefreshStatus={handleRefreshStatus}
              />
              <SellerListingsSummaryMetrics summary={summary} />
            </>
          ) : null}

          <SellerListingsFilters filters={filters} onChange={handleFilterChange} onReset={handleResetFilters} />

          {listingsError ? (
            <Alert variant="destructive">
              <AlertTitle>Listings unavailable</AlertTitle>
              <AlertDescription>{listingsError}</AlertDescription>
            </Alert>
          ) : (
            <SellerListingsTable
              items={collection?.items ?? []}
              total={collection?.total ?? 0}
              offset={collection?.offset ?? offset}
              limit={collection?.limit ?? PAGE_SIZE}
              loading={listingsLoading}
              hasEverSynchronized={summary ? summary.sync.status !== "never_synchronized" : false}
              onPageChange={handlePageChange}
              onOpenDetail={handleOpenDetail}
            />
          )}
        </>
      )}

      {listingId && participationId ? (
        <SellerListingsDetail
          participationId={participationId}
          listingId={listingId}
          refreshToken={refreshToken}
          onClose={handleCloseDetail}
        />
      ) : null}
    </div>
  );
}
