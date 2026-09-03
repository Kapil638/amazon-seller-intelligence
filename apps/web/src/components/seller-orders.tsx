"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Loader2, RefreshCw } from "lucide-react";

import { SellerListingsMarketplaceSelector } from "@/components/seller-listings-marketplace-selector";
import {
  AmazonConnectionError,
  OrdersApiError,
  fetchAmazonConnection,
  fetchOrderDetail,
  fetchOrders,
  fetchOrdersSummary,
  triggerOrdersSync,
} from "@/lib/api";
import { CANONICAL_MARKETPLACE_ID, formatDateTime, formatPrice } from "@/lib/seller-listings-view";
import {
  ORDERS_SYNC_STATUS_LABEL,
  formatFulfillmentStatus,
  formatOrdersImportedCount,
  ordersMoreHistoryRemains,
  ordersSyncShowsActiveSpinner,
} from "@/lib/seller-orders-view";
import type { AmazonSellerMarketplace, OrderCollectionItem, OrderDetail, OrdersSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

type AddressableMarketplace = AmazonSellerMarketplace & { id: string };

const PAGE_SIZE = 25;
const POLL_INITIAL_MS = 3000;
const POLL_MAX_MS = 20000;
const POLL_FACTOR = 1.5;

function parsePage(searchParams: URLSearchParams): number {
  const raw = Number(searchParams.get("page") ?? "1");
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1;
}

export function SellerOrders() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [sellerAccountId, setSellerAccountId] = useState<string | null>(null);
  const [marketplaces, setMarketplaces] = useState<AddressableMarketplace[]>([]);
  const [connectionLoading, setConnectionLoading] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const participationId = searchParams.get("participation");
  const page = parsePage(searchParams);
  const offset = (page - 1) * PAGE_SIZE;

  const [summary, setSummary] = useState<OrdersSummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [collectionItems, setCollectionItems] = useState<OrderCollectionItem[]>([]);
  const [collectionTotal, setCollectionTotal] = useState(0);
  const [collectionLoading, setCollectionLoading] = useState(false);
  const [collectionError, setCollectionError] = useState<string | null>(null);

  const [triggering, setTriggering] = useState(false);
  const [triggerMessage, setTriggerMessage] = useState<string | null>(null);

  const [detail, setDetail] = useState<OrderDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const orderId = searchParams.get("order");

  const replaceParams = useCallback(
    (mutate: (params: URLSearchParams) => void) => {
      const next = new URLSearchParams(searchParams.toString());
      mutate(next);
      const query = next.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  // 1. Resolve the connection's marketplace participations — the only
  // source of marketplace_participation_id used here, exactly like the
  // Listings page.
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

  // 2. Default participation selection: URL first, else canonical, else
  // the first available. (Unlike Listings' "most-recently-synced"
  // optimization, this foundation milestone keeps default selection
  // simple — every marketplace is equally reachable via the selector.)
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
    fetchOrdersSummary(participationId)
      .then((next) => {
        setSummary(next);
        setSummaryError(null);
      })
      .catch((err: unknown) => {
        setSummaryError(err instanceof OrdersApiError ? err.message : "Orders summary could not be loaded.");
      });
  }, [participationId]);

  const loadCollection = useCallback(() => {
    if (!participationId) return;
    setCollectionLoading(true);
    fetchOrders(participationId, { offset, limit: PAGE_SIZE })
      .then((result) => {
        setCollectionItems(result.items);
        setCollectionTotal(result.total);
        setCollectionError(null);
      })
      .catch((err: unknown) => {
        setCollectionError(err instanceof OrdersApiError ? err.message : "Orders could not be loaded.");
      })
      .finally(() => setCollectionLoading(false));
  }, [participationId, offset]);

  // 3. Fetch summary + collection whenever the selected participation or
  // page changes.
  useEffect(() => {
    loadSummary();
    loadCollection();
  }, [loadSummary, loadCollection]);

  // 4. Rediscover an in-flight job purely from the summary's own sync
  // evidence — no client-side run-id storage, matching Listings'
  // established pattern (a page reload never loses track of progress
  // because the server, not the browser, is the source of truth). The
  // summary endpoint's own sync evidence already carries every field the
  // progress strip needs, so polling re-fetches it directly rather than
  // tracking a separate client-side run id.

  // 5. Adaptive-backoff polling while a sync is non-terminal.
  useEffect(() => {
    const status = summary?.sync.status;
    if (status !== "queued" && status !== "running" && status !== "waiting_to_retry") return;
    let cancelled = false;
    let delay = POLL_INITIAL_MS;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      if (cancelled) return;
      loadSummary();
      loadCollection();
      delay = Math.min(delay * POLL_FACTOR, POLL_MAX_MS);
      timer = setTimeout(tick, delay);
    };
    timer = setTimeout(tick, delay);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [summary?.sync.status]);

  // 6. Order detail drawer, driven by the `order` query param.
  useEffect(() => {
    if (!participationId || !orderId) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    fetchOrderDetail(participationId, orderId)
      .then((next) => {
        if (!cancelled) {
          setDetail(next);
          setDetailError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setDetail(null);
          setDetailError(err instanceof OrdersApiError ? err.message : "This order was not found.");
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [participationId, orderId]);

  const handleSync = useCallback(async () => {
    if (!sellerAccountId || !participationId) return;
    setTriggering(true);
    setTriggerMessage(null);
    try {
      const outcome = await triggerOrdersSync(sellerAccountId, [participationId]);
      if (outcome.reason === "queued") {
        loadSummary();
      } else {
        setTriggerMessage(outcome.message ?? "Orders synchronization could not be started.");
      }
    } catch {
      setTriggerMessage("Could not reach the server to start synchronization.");
    } finally {
      setTriggering(false);
    }
  }, [sellerAccountId, participationId, loadSummary]);

  const totalPages = Math.max(1, Math.ceil(collectionTotal / PAGE_SIZE));
  const syncStatus = summary?.sync.status ?? "never_synchronized";
  const moreHistoryRemains = ordersMoreHistoryRemains(syncStatus, summary?.sync.pagination_complete ?? null);

  if (connectionLoading) {
    return <p className="text-sm text-muted-foreground">Loading Orders…</p>;
  }
  if (connectionError) {
    return <p className="text-sm text-destructive">{connectionError}</p>;
  }
  if (marketplaces.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No Amazon marketplace is connected yet. Connect a seller account to see Orders here.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <SellerListingsMarketplaceSelector
          marketplaces={marketplaces}
          selectedId={participationId}
          onChange={(id) =>
            replaceParams((params) => {
              params.set("participation", id);
              params.delete("page");
              params.delete("order");
            })
          }
        />
        <button
          type="button"
          onClick={handleSync}
          disabled={triggering || ordersSyncShowsActiveSpinner(syncStatus)}
          className="inline-flex h-10 items-center gap-2 rounded-md border border-input bg-surface px-4 text-sm font-medium transition-colors hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-60"
        >
          {ordersSyncShowsActiveSpinner(syncStatus) || triggering ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Sync Orders
        </button>
      </div>

      {/* Truthful progress strip — customer-friendly language only, never
          page tokens, leases, or raw worker/status terminology. */}
      <div
        className={cn(
          "rounded-md border px-4 py-3 text-sm",
          syncStatus === "failed" || syncStatus === "partial" || syncStatus === "timed_out"
            ? "border-destructive/40 bg-destructive/5 text-destructive"
            : "border-border bg-surface-muted text-muted-foreground",
        )}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-medium text-foreground">{ORDERS_SYNC_STATUS_LABEL[syncStatus]}</span>
          {summary && summary.sync.orders_accepted !== null && (syncStatus === "running" || syncStatus === "succeeded") && (
            <span>· {formatOrdersImportedCount(summary.sync.orders_accepted)}</span>
          )}
          {summary?.sync.last_successful_synchronized_at && (
            <span>· Data processed through {formatDateTime(summary.sync.last_successful_synchronized_at)}</span>
          )}
          {moreHistoryRemains && <span>· More history remains</span>}
        </div>
        {triggerMessage && <p className="mt-1 text-xs">{triggerMessage}</p>}
      </div>

      {summaryError && <p className="text-sm text-destructive">{summaryError}</p>}

      {summary && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MetricCard label="Total orders" value={summary.total_orders.toLocaleString()} />
          <MetricCard label="Cancelled" value={summary.cancelled_count.toLocaleString()} />
          <MetricCard
            label="Order value"
            value={
              summary.order_value_sum
                ? formatPrice(summary.order_value_sum, summary.order_value_currency)
                : summary.total_orders > 0
                  ? "Mixed currencies"
                  : "—"
            }
          />
          <MetricCard label="Prime orders" value={summary.prime_order_count.toLocaleString()} />
        </div>
      )}

      {collectionError && <p className="text-sm text-destructive">{collectionError}</p>}
      {!collectionLoading && collectionItems.length === 0 && !collectionError && (
        <p className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          {syncStatus === "never_synchronized"
            ? "No orders yet. Run a sync to import Orders for this marketplace."
            : "No orders found for this view."}
        </p>
      )}
      {collectionItems.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-surface-muted text-left text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-3 py-2">Order</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2">Total</th>
                <th className="px-3 py-2">Items</th>
                <th className="px-3 py-2">Updated</th>
              </tr>
            </thead>
            <tbody>
              {collectionItems.map((item) => (
                <tr
                  key={item.id}
                  className="cursor-pointer border-t border-border hover:bg-surface-muted"
                  onClick={() =>
                    replaceParams((params) => {
                      params.set("order", item.id);
                    })
                  }
                >
                  <td className="px-3 py-2 font-medium">{item.amazon_order_id}</td>
                  <td className="px-3 py-2">{formatFulfillmentStatus(item.fulfillment_status)}</td>
                  <td className="px-3 py-2">{formatPrice(item.order_total_amount, item.order_total_currency)}</td>
                  <td className="px-3 py-2">{item.item_count}</td>
                  <td className="px-3 py-2 text-muted-foreground">{formatDateTime(item.amazon_last_updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {collectionTotal > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            Page {page} of {totalPages}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => replaceParams((params) => params.set("page", String(page - 1)))}
              className="rounded-md border border-input px-3 py-1.5 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => replaceParams((params) => params.set("page", String(page + 1)))}
              className="rounded-md border border-input px-3 py-1.5 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {orderId && (
        <OrderDetailDrawer
          loading={detailLoading}
          error={detailError}
          detail={detail}
          onClose={() =>
            replaceParams((params) => {
              params.delete("order");
            })
          }
        />
      )}
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

function OrderDetailDrawer({
  loading,
  error,
  detail,
  onClose,
}: {
  loading: boolean;
  error: string | null;
  detail: OrderDetail | null;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40">
      <button type="button" aria-label="Close" className="absolute inset-0" onClick={onClose} />
      <div role="dialog" aria-modal="true" className="relative flex h-full w-full max-w-md flex-col overflow-y-auto border-l border-border bg-surface p-5 shadow-2xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold">Order detail</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-md px-2 py-1 text-sm text-muted-foreground hover:bg-surface-muted">
            Close
          </button>
        </div>
        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {error && <p className="text-sm text-destructive">{error} was not found.</p>}
        {detail && !loading && !error && (
          <div className="flex flex-col gap-4 text-sm">
            <div>
              <p className="text-xs text-muted-foreground">Amazon order ID</p>
              <p className="font-medium">{detail.amazon_order_id}</p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-xs text-muted-foreground">Status</p>
                <p>{formatFulfillmentStatus(detail.fulfillment_status)}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Fulfilled by</p>
                <p>{detail.fulfilled_by ?? "—"}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Total</p>
                <p>{formatPrice(detail.order_total_amount, detail.order_total_currency)}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Placed</p>
                <p>{formatDateTime(detail.amazon_created_at)}</p>
              </div>
            </div>
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Items</p>
              <div className="flex flex-col gap-2">
                {detail.items.map((item) => (
                  <div key={item.id} className="rounded-md border border-border p-2.5">
                    <p className="font-medium">{item.item_name ?? item.seller_sku}</p>
                    <p className="text-xs text-muted-foreground">
                      SKU {item.seller_sku}
                      {item.asin ? ` · ASIN ${item.asin}` : ""}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      Qty {item.quantity_ordered} · {formatPrice(item.unit_price_amount, item.unit_price_currency)}
                    </p>
                  </div>
                ))}
                {detail.items.length === 0 && <p className="text-xs text-muted-foreground">No item detail available.</p>}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
