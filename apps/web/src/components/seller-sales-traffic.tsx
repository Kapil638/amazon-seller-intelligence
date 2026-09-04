"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Loader2, RefreshCw, TrendingUp } from "lucide-react";

import { SellerListingsMarketplaceSelector } from "@/components/seller-listings-marketplace-selector";
import {
  AmazonConnectionError,
  SalesTrafficApiError,
  fetchAmazonConnection,
  fetchSalesTrafficDailyTrend,
  fetchSalesTrafficFreshness,
  fetchSalesTrafficProducts,
  fetchSalesTrafficSummary,
  triggerSalesTrafficSync,
} from "@/lib/api";
import { CANONICAL_MARKETPLACE_ID, formatDate, formatDateTime, formatPrice } from "@/lib/seller-listings-view";
import {
  formatSalesTrafficCount,
  formatSalesTrafficFailureReason,
  formatSalesTrafficPercentage,
  isHighTrafficLowConversion,
  salesTrafficSyncIsNonTerminal,
  salesTrafficSyncShowsActiveSpinner,
  SALES_TRAFFIC_SYNC_STATUS_LABEL,
} from "@/lib/seller-sales-traffic-view";
import type {
  AmazonSellerMarketplace,
  SalesTrafficDailyTrendPoint,
  SalesTrafficFreshness,
  SalesTrafficProductRow,
  SalesTrafficSummary,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type AddressableMarketplace = AmazonSellerMarketplace & { id: string };

const PRODUCT_PAGE_SIZE = 25;
const POLL_INITIAL_MS = 3000;
const POLL_MAX_MS = 20000;
const POLL_FACTOR = 1.5;
// No worker is deployed anywhere in production yet (this milestone's own
// known limitation) — a "queued" job may realistically sit unclaimed
// indefinitely. Polling it forever would be a dishonest, resource-wasting
// "this is actively being worked on" signal. Auto-polling gives up after
// this many ticks *while still queued* (never having reached "running")
// and hands control to an explicit "Refresh status" button instead. A
// job that reaches "running" or "waiting_to_retry" keeps polling — those
// states mean a worker genuinely claimed it and is doing something.
const MAX_QUEUED_POLL_TICKS = 8;

const PERIOD_OPTIONS = [
  { value: "7", label: "Last 7 days" },
  { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" },
] as const;
type PeriodValue = (typeof PERIOD_OPTIONS)[number]["value"];

function toIsoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** `end` is today (UTC); `start` is `periodDays - 1` days before it, so a
 * "Last 7 days" selection covers exactly 7 calendar days inclusive of
 * today. Amazon's own report-settlement lag (the most recent 1-2 days
 * can under-report) is a backend/backfill concern, not hidden here — the
 * freshness panel surfaces the real `latest_daily_fact_date` instead of
 * pretending "today" always has data. */
function periodRange(periodDays: number): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - (periodDays - 1));
  return { start: toIsoDate(start), end: toIsoDate(end) };
}

function parsePeriod(searchParams: URLSearchParams): PeriodValue {
  const raw = searchParams.get("period");
  return (PERIOD_OPTIONS.find((option) => option.value === raw)?.value ?? "30") as PeriodValue;
}

function parseProductPage(searchParams: URLSearchParams): number {
  const raw = Number(searchParams.get("productPage") ?? "1");
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1;
}

export function SellerSalesTraffic() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [sellerAccountId, setSellerAccountId] = useState<string | null>(null);
  const [marketplaces, setMarketplaces] = useState<AddressableMarketplace[]>([]);
  const [connectionLoading, setConnectionLoading] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const participationId = searchParams.get("participation");
  const period = parsePeriod(searchParams);
  const { start, end } = useMemo(() => periodRange(Number(period)), [period]);
  const productSearch = searchParams.get("q") ?? "";
  const productPage = parseProductPage(searchParams);
  const productOffset = (productPage - 1) * PRODUCT_PAGE_SIZE;

  const [summary, setSummary] = useState<SalesTrafficSummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [trendPoints, setTrendPoints] = useState<SalesTrafficDailyTrendPoint[]>([]);
  const [trendError, setTrendError] = useState<string | null>(null);
  const [freshness, setFreshness] = useState<SalesTrafficFreshness | null>(null);
  const [productItems, setProductItems] = useState<SalesTrafficProductRow[]>([]);
  const [productTotal, setProductTotal] = useState(0);
  const [productLoading, setProductLoading] = useState(false);
  const [productError, setProductError] = useState<string | null>(null);

  const [triggering, setTriggering] = useState(false);
  const [triggerMessage, setTriggerMessage] = useState<string | null>(null);
  const [queuedPollGaveUp, setQueuedPollGaveUp] = useState(false);

  // Guards against a rapid marketplace switch racing an in-flight fetch
  // for the *previous* selection — a response for a since-abandoned
  // participation must never overwrite state for the one now showing.
  // Kept in a ref (not state) so the check inside an already-in-flight
  // `.then()` always reads the *current* selection, not the value
  // captured when the fetch started.
  const currentParticipationIdRef = useRef(participationId);
  useEffect(() => {
    currentParticipationIdRef.current = participationId;
  }, [participationId]);

  const replaceParams = useCallback(
    (mutate: (params: URLSearchParams) => void) => {
      const next = new URLSearchParams(searchParams.toString());
      mutate(next);
      const query = next.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  // 1. Resolve the connection's marketplace participations — same source
  // as Orders/Listings.
  useEffect(() => {
    let cancelled = false;
    setConnectionLoading(true);
    setConnectionError(null);
    fetchAmazonConnection()
      .then((overview) => {
        if (cancelled) return;
        setSellerAccountId(overview.seller_account_id ?? null);
        const addressable = (overview.marketplaces ?? []).filter((m): m is AddressableMarketplace => Boolean(m.id));
        setMarketplaces(addressable);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setConnectionError(err instanceof AmazonConnectionError ? err.message : "Amazon Connection could not be reached.");
      })
      .finally(() => {
        if (!cancelled) setConnectionLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 2. Default participation selection — same convention as Orders.
  useEffect(() => {
    if (connectionLoading || marketplaces.length === 0) return;
    if (marketplaces.some((m) => m.id === participationId)) return;
    const canonical = marketplaces.find((m) => m.marketplace_id === CANONICAL_MARKETPLACE_ID);
    replaceParams((params) => {
      params.set("participation", (canonical ?? marketplaces[0]).id);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectionLoading, marketplaces, participationId]);

  const loadSummary = useCallback(() => {
    if (!participationId) return;
    const requestedFor = participationId;
    fetchSalesTrafficSummary(participationId, start, end)
      .then((next) => {
        if (currentParticipationIdRef.current !== requestedFor) return; // stale — a different marketplace is now selected
        setSummary(next);
        setSummaryError(null);
      })
      .catch((err: unknown) => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setSummaryError(err instanceof SalesTrafficApiError ? err.message : "Summary could not be loaded.");
      });
  }, [participationId, start, end]);

  const loadTrend = useCallback(() => {
    if (!participationId) return;
    const requestedFor = participationId;
    fetchSalesTrafficDailyTrend(participationId, start, end)
      .then((next) => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setTrendPoints(next.points);
        setTrendError(null);
      })
      .catch((err: unknown) => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setTrendError(err instanceof SalesTrafficApiError ? err.message : "Daily trend could not be loaded.");
      });
  }, [participationId, start, end]);

  const loadFreshness = useCallback(() => {
    if (!participationId) return;
    const requestedFor = participationId;
    fetchSalesTrafficFreshness(participationId)
      .then((next) => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setFreshness(next);
      })
      .catch(() => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setFreshness(null);
      });
  }, [participationId]);

  const loadProducts = useCallback(() => {
    if (!participationId) return;
    const requestedFor = participationId;
    setProductLoading(true);
    fetchSalesTrafficProducts(participationId, start, end, {
      q: productSearch || undefined,
      offset: productOffset,
      limit: PRODUCT_PAGE_SIZE,
    })
      .then((result) => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setProductItems(result.items);
        setProductTotal(result.total);
        setProductError(null);
      })
      .catch((err: unknown) => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setProductError(err instanceof SalesTrafficApiError ? err.message : "Products could not be loaded.");
      })
      .finally(() => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setProductLoading(false);
      });
  }, [participationId, start, end, productSearch, productOffset]);

  // 3. Fetch everything whenever the selected participation/period/search
  // changes.
  useEffect(() => {
    loadSummary();
    loadTrend();
    loadFreshness();
  }, [loadSummary, loadTrend, loadFreshness]);

  useEffect(() => {
    loadProducts();
  }, [loadProducts]);

  // 4. Adaptive-backoff polling while a sync is non-terminal — no
  // client-side run-id storage, rediscovered purely from the summary's
  // own sync evidence on every reload (a page refresh never loses track
  // of progress), exactly like Orders/Listings. Unlike Orders/Listings,
  // this stops on its own after MAX_QUEUED_POLL_TICKS while the status
  // never advances past "queued" (see MAX_QUEUED_POLL_TICKS's own
  // docstring) — "running"/"waiting_to_retry" keep polling without a
  // tick limit, since those genuinely indicate worker activity.
  useEffect(() => {
    setQueuedPollGaveUp(false);
  }, [participationId]);

  useEffect(() => {
    const status = summary?.sync.status;
    if (!status || !salesTrafficSyncIsNonTerminal(status) || queuedPollGaveUp) return;
    let cancelled = false;
    let delay = POLL_INITIAL_MS;
    let queuedTicks = 0;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      if (cancelled) return;
      loadSummary();
      loadFreshness();
      if (status === "queued") {
        queuedTicks += 1;
        if (queuedTicks >= MAX_QUEUED_POLL_TICKS) {
          setQueuedPollGaveUp(true);
          return; // stop scheduling further ticks — a manual refresh takes over
        }
      }
      delay = Math.min(delay * POLL_FACTOR, POLL_MAX_MS);
      timer = setTimeout(tick, delay);
    };
    timer = setTimeout(tick, delay);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary?.sync.status, queuedPollGaveUp]);

  const handleManualRefresh = useCallback(() => {
    setQueuedPollGaveUp(false); // a fresh manual check earns a fresh polling budget if still queued
    loadSummary();
    loadFreshness();
  }, [loadSummary, loadFreshness]);

  const handleSync = useCallback(async () => {
    if (!sellerAccountId || !participationId) return;
    setTriggering(true);
    setTriggerMessage(null);
    try {
      const outcome = await triggerSalesTrafficSync(sellerAccountId, participationId, start, end);
      if (outcome.reason === "queued") {
        loadSummary();
      } else {
        setTriggerMessage(outcome.message ?? "Sales and Traffic synchronization could not be started.");
      }
    } catch {
      setTriggerMessage("Could not reach the server to start synchronization.");
    } finally {
      setTriggering(false);
    }
  }, [sellerAccountId, participationId, start, end, loadSummary]);

  const totalProductPages = Math.max(1, Math.ceil(productTotal / PRODUCT_PAGE_SIZE));
  const syncStatus = summary?.sync.status ?? "never_synchronized";
  const isFailureState = syncStatus === "failed" || syncStatus === "partial" || syncStatus === "timed_out";

  if (connectionLoading) {
    return <p className="text-sm text-muted-foreground">Loading Sales & Traffic…</p>;
  }
  if (connectionError) {
    return <p className="text-sm text-destructive">{connectionError}</p>;
  }
  if (marketplaces.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No Amazon marketplace is connected yet. Connect a seller account to see Sales & Traffic here.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-wrap items-end gap-3">
          <SellerListingsMarketplaceSelector
            marketplaces={marketplaces}
            selectedId={participationId}
            onChange={(id) =>
              replaceParams((params) => {
                params.set("participation", id);
                params.delete("productPage");
              })
            }
          />
          <div className="flex flex-col gap-1">
            <span className="text-xs text-muted-foreground">Period</span>
            <div className="flex rounded-md border border-input">
              {PERIOD_OPTIONS.map((option, index) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => replaceParams((params) => params.set("period", option.value))}
                  className={cn(
                    "px-3 py-1.5 text-sm transition-colors",
                    index > 0 && "border-l border-input",
                    period === option.value
                      ? "bg-primary text-primary-foreground"
                      : "bg-surface text-muted-foreground hover:bg-surface-muted",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={handleSync}
          disabled={triggering || salesTrafficSyncShowsActiveSpinner(syncStatus)}
          className="inline-flex h-10 items-center gap-2 rounded-md border border-input bg-surface px-4 text-sm font-medium transition-colors hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-60"
        >
          {salesTrafficSyncShowsActiveSpinner(syncStatus) || triggering ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Sync Sales & Traffic
        </button>
      </div>

      {/* Truthful progress strip. A "queued" job never shows the spinner
          (see salesTrafficSyncShowsActiveSpinner's own docstring) — only
          "running" does, and only once a worker has actually claimed it. */}
      <div
        className={cn(
          "rounded-md border px-4 py-3 text-sm",
          isFailureState ? "border-destructive/40 bg-destructive/5 text-destructive" : "border-border bg-surface-muted text-muted-foreground",
        )}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-medium text-foreground">{SALES_TRAFFIC_SYNC_STATUS_LABEL[syncStatus]}</span>
          {syncStatus === "queued" && <span>· Waiting for a worker to pick this up</span>}
          {summary?.sync.report_processing_status && syncStatus === "running" && (
            <span>· {summary.sync.report_processing_status === "IN_PROGRESS" ? "Amazon is generating this report" : "Waiting in Amazon's queue"}</span>
          )}
          {summary?.sync.last_successful_synchronized_at && (
            <span>· Last synchronized {formatDateTime(summary.sync.last_successful_synchronized_at)}</span>
          )}
        </div>
        {isFailureState && (
          <p className="mt-1 text-xs">{formatSalesTrafficFailureReason(summary?.sync.failure_class ?? null)}</p>
        )}
        {syncStatus === "queued" && queuedPollGaveUp && (
          <div className="mt-2 flex items-center gap-2">
            <p className="text-xs">
              Still queued after a while — no worker has picked this up yet. Automatic checking has paused.
            </p>
            <button
              type="button"
              onClick={handleManualRefresh}
              className="inline-flex items-center gap-1 rounded-md border border-input bg-surface px-2 py-1 text-xs font-medium hover:bg-surface-muted"
            >
              <RefreshCw className="h-3 w-3" />
              Refresh status
            </button>
          </div>
        )}
        {triggerMessage && <p className="mt-1 text-xs">{triggerMessage}</p>}
      </div>

      {freshness && (freshness.earliest_daily_fact_date || freshness.latest_daily_fact_date) && (
        <p className="text-xs text-muted-foreground">
          Coverage on file: {formatDate(freshness.earliest_daily_fact_date)} – {formatDate(freshness.latest_daily_fact_date)}
          {freshness.sync.synced_through_date && (
            <> · Daily product data synced through {formatDate(freshness.sync.synced_through_date)}</>
          )}
        </p>
      )}

      {summaryError && <p className="text-sm text-destructive">{summaryError}</p>}

      {summary && syncStatus !== "never_synchronized" && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <MetricCard
              label="Ordered product sales"
              value={
                summary.ordered_product_sales_amount
                  ? formatPrice(summary.ordered_product_sales_amount, summary.currency_code)
                  : "—"
              }
            />
            <MetricCard label="Units ordered" value={formatSalesTrafficCount(summary.units_ordered)} />
            <MetricCard label="Sessions" value={formatSalesTrafficCount(summary.sessions)} />
            <MetricCard label="Page views" value={formatSalesTrafficCount(summary.page_views)} />
            <MetricCard label="Buy Box %" value={formatSalesTrafficPercentage(summary.buy_box_percentage)} />
            <MetricCard label="Unit session %" value={formatSalesTrafficPercentage(summary.unit_session_percentage)} />
          </div>
          {/* Distinguishes a genuinely empty, successfully-swept period
              from "never synchronized" — the sync above already succeeded;
              there is simply nothing to report for these dates. */}
          {syncStatus === "succeeded" && summary.days_with_data === 0 && (
            <p className="text-xs text-muted-foreground">
              This marketplace was synchronized successfully — no sales or traffic was recorded for this period.
            </p>
          )}
        </>
      )}

      {syncStatus === "never_synchronized" && (
        <p className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          No Sales & Traffic data yet. Run a sync to import Amazon&apos;s Sales and Traffic Business Report for this
          marketplace and period.
        </p>
      )}

      {/* Daily trend */}
      {trendError && <p className="text-sm text-destructive">{trendError}</p>}
      {trendPoints.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="text-sm font-semibold">Daily trend</h2>
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="bg-surface-muted text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Date</th>
                  <th className="px-3 py-2">Sales</th>
                  <th className="px-3 py-2">Units</th>
                  <th className="px-3 py-2">Sessions</th>
                  <th className="px-3 py-2">Page views</th>
                  <th className="px-3 py-2">Buy Box %</th>
                  <th className="px-3 py-2">Unit session %</th>
                </tr>
              </thead>
              <tbody>
                {trendPoints.map((point) => (
                  <tr key={point.report_date} className="border-t border-border">
                    <td className="px-3 py-2 font-medium">{formatDate(point.report_date)}</td>
                    <td className="px-3 py-2">{formatPrice(point.ordered_product_sales_amount, point.currency_code)}</td>
                    <td className="px-3 py-2">{formatSalesTrafficCount(point.units_ordered)}</td>
                    <td className="px-3 py-2">{formatSalesTrafficCount(point.sessions)}</td>
                    <td className="px-3 py-2">{formatSalesTrafficCount(point.page_views)}</td>
                    <td className="px-3 py-2">{formatSalesTrafficPercentage(point.buy_box_percentage)}</td>
                    <td className="px-3 py-2">{formatSalesTrafficPercentage(point.unit_session_percentage)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Product performance / traffic-vs-conversion */}
      <section className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Product performance</h2>
          <input
            type="search"
            value={productSearch}
            onChange={(event) =>
              replaceParams((params) => {
                if (event.target.value) params.set("q", event.target.value);
                else params.delete("q");
                params.delete("productPage");
              })
            }
            placeholder="Search ASIN or SKU"
            className="h-9 w-56 rounded-md border border-input bg-surface px-3 text-sm"
          />
        </div>

        {productError && <p className="text-sm text-destructive">{productError}</p>}
        {!productLoading && productItems.length === 0 && !productError && (
          <p className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
            {syncStatus === "never_synchronized"
              ? "No product data yet. Run a sync to import product-level Sales & Traffic facts."
              : "No products found for this view."}
          </p>
        )}
        {productItems.some((item) => !item.coverage_complete) && (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700">
            Some products below only have partial data for this period — see the Coverage column for exactly which
            dates are covered. Numbers for those products reflect only the covered dates, never the full period.
          </p>
        )}
        {productItems.length > 0 && (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="bg-surface-muted text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Product</th>
                  <th className="px-3 py-2">Sales</th>
                  <th className="px-3 py-2">Units</th>
                  <th className="px-3 py-2">Sessions</th>
                  <th className="px-3 py-2">Buy Box %</th>
                  <th className="px-3 py-2">Unit session %</th>
                  <th className="px-3 py-2">Coverage</th>
                  <th className="px-3 py-2">Signal</th>
                </tr>
              </thead>
              <tbody>
                {productItems.map((item) => (
                  <tr key={`${item.parent_asin}-${item.child_asin ?? ""}-${item.seller_sku ?? ""}`} className="border-t border-border">
                    <td className="px-3 py-2">
                      <p className="font-medium">{item.item_name ?? item.seller_sku ?? item.parent_asin}</p>
                      <p className="text-xs text-muted-foreground">
                        {item.seller_sku ? `SKU ${item.seller_sku} · ` : ""}
                        ASIN {item.child_asin ?? item.parent_asin}
                      </p>
                    </td>
                    <td className="px-3 py-2">{formatPrice(item.ordered_product_sales_amount, item.currency_code)}</td>
                    <td className="px-3 py-2">{formatSalesTrafficCount(item.units_ordered)}</td>
                    <td className="px-3 py-2">{formatSalesTrafficCount(item.sessions)}</td>
                    <td className="px-3 py-2">{formatSalesTrafficPercentage(item.buy_box_percentage)}</td>
                    <td className="px-3 py-2">{formatSalesTrafficPercentage(item.unit_session_percentage)}</td>
                    <td className="px-3 py-2">
                      {item.coverage_complete ? (
                        <span className="text-xs text-muted-foreground">Full</span>
                      ) : (
                        <span
                          className="inline-flex cursor-help items-center rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700"
                          title={
                            (item.partial_coverage_reason ?? "Incomplete coverage") +
                            (item.covered_ranges.length > 0
                              ? ` — covers ${item.covered_ranges.map((r) => `${formatDate(r.start)}–${formatDate(r.end)}`).join(", ")}`
                              : "")
                          }
                        >
                          Partial
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {isHighTrafficLowConversion(item.sessions, item.unit_session_percentage) && (
                        <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-600">
                          <TrendingUp className="h-3 w-3" />
                          High traffic, low conversion
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {productTotal > PRODUCT_PAGE_SIZE && (
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>
              Page {productPage} of {totalProductPages}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={productPage <= 1}
                onClick={() => replaceParams((params) => params.set("productPage", String(productPage - 1)))}
                className="rounded-md border border-input px-3 py-1.5 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={productPage >= totalProductPages}
                onClick={() => replaceParams((params) => params.set("productPage", String(productPage + 1)))}
                className="rounded-md border border-input px-3 py-1.5 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-surface p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </div>
  );
}
