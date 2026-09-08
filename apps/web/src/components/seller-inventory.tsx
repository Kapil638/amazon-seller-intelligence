"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Loader2, RefreshCw } from "lucide-react";

import { SellerListingsMarketplaceSelector } from "@/components/seller-listings-marketplace-selector";
import {
  AmazonConnectionError,
  InventoryApiError,
  fetchAmazonConnection,
  fetchInventory,
  fetchInventorySummary,
  triggerInventorySync,
} from "@/lib/api";
import { CANONICAL_MARKETPLACE_ID, formatDateTime } from "@/lib/seller-listings-view";
import {
  formatInventoryFailureReason,
  formatInventoryQuantity,
  inventorySyncIsNonTerminal,
  inventorySyncShowsActiveSpinner,
  INVENTORY_SYNC_STATUS_LABEL,
} from "@/lib/seller-inventory-view";
import type { AmazonSellerMarketplace, InventoryCollectionItem, InventorySummary } from "@/lib/types";
import { cn } from "@/lib/utils";

type AddressableMarketplace = AmazonSellerMarketplace & { id: string };

const PAGE_SIZE = 25;
const POLL_INITIAL_MS = 3000;
const POLL_MAX_MS = 20000;
const POLL_FACTOR = 1.5;
// Mirrors SellerSalesTraffic's own MAX_QUEUED_POLL_TICKS reasoning: no
// worker is deployed anywhere in production yet, so a "queued" job may
// realistically sit unclaimed indefinitely — auto-polling gives up after
// this many ticks while still queued and hands control to an explicit
// "Refresh status" button instead.
const MAX_QUEUED_POLL_TICKS = 8;

function parsePage(searchParams: URLSearchParams): number {
  const raw = Number(searchParams.get("invPage") ?? "1");
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1;
}

export function SellerInventory() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [marketplaces, setMarketplaces] = useState<AddressableMarketplace[]>([]);
  const [connectionLoading, setConnectionLoading] = useState(true);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const participationId = searchParams.get("participation");
  const search = searchParams.get("q") ?? "";
  const page = parsePage(searchParams);
  const offset = (page - 1) * PAGE_SIZE;

  const [summary, setSummary] = useState<InventorySummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [items, setItems] = useState<InventoryCollectionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [triggering, setTriggering] = useState(false);
  const [triggerMessage, setTriggerMessage] = useState<string | null>(null);
  const [queuedPollGaveUp, setQueuedPollGaveUp] = useState(false);

  // Guards against a rapid marketplace switch racing an in-flight fetch
  // for the *previous* selection — see SellerSalesTraffic's identical
  // pattern/reasoning.
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

  useEffect(() => {
    let cancelled = false;
    setConnectionLoading(true);
    setConnectionError(null);
    fetchAmazonConnection()
      .then((overview) => {
        if (cancelled) return;
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
    fetchInventorySummary(participationId)
      .then((next) => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setSummary(next);
        setSummaryError(null);
      })
      .catch((err: unknown) => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setSummaryError(err instanceof InventoryApiError ? err.message : "Summary could not be loaded.");
      });
  }, [participationId]);

  const loadItems = useCallback(() => {
    if (!participationId) return;
    const requestedFor = participationId;
    setListLoading(true);
    fetchInventory(participationId, { q: search || undefined, offset, limit: PAGE_SIZE })
      .then((result) => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setItems(result.items);
        setTotal(result.total);
        setListError(null);
      })
      .catch((err: unknown) => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setListError(err instanceof InventoryApiError ? err.message : "Inventory could not be loaded.");
      })
      .finally(() => {
        if (currentParticipationIdRef.current !== requestedFor) return;
        setListLoading(false);
      });
  }, [participationId, search, offset]);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  useEffect(() => {
    loadItems();
  }, [loadItems]);

  // Adaptive-backoff polling while a sync is non-terminal — mirrors
  // SellerSalesTraffic's identical pattern exactly.
  useEffect(() => {
    setQueuedPollGaveUp(false);
  }, [participationId]);

  useEffect(() => {
    const status = summary?.sync.status;
    if (!status || !inventorySyncIsNonTerminal(status) || queuedPollGaveUp) return;
    let cancelled = false;
    let delay = POLL_INITIAL_MS;
    let queuedTicks = 0;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      if (cancelled) return;
      loadSummary();
      if (status === "queued") {
        queuedTicks += 1;
        if (queuedTicks >= MAX_QUEUED_POLL_TICKS) {
          setQueuedPollGaveUp(true);
          return;
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

  // A sync that just succeeded should also refresh the product list, not
  // only the summary tile counts.
  const previousStatusRef = useRef(summary?.sync.status);
  useEffect(() => {
    const status = summary?.sync.status;
    if (status === "succeeded" && previousStatusRef.current !== "succeeded") {
      loadItems();
    }
    previousStatusRef.current = status;
  }, [summary?.sync.status, loadItems]);

  const handleManualRefresh = useCallback(() => {
    setQueuedPollGaveUp(false);
    loadSummary();
  }, [loadSummary]);

  const handleSync = useCallback(async () => {
    if (!participationId) return;
    setTriggering(true);
    setTriggerMessage(null);
    try {
      const outcome = await triggerInventorySync(participationId);
      if (outcome.reason === "queued") {
        loadSummary();
      } else {
        setTriggerMessage(outcome.message ?? "FBA Inventory synchronization could not be started.");
      }
    } catch {
      setTriggerMessage("Could not reach the server to start synchronization.");
    } finally {
      setTriggering(false);
    }
  }, [participationId, loadSummary]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const syncStatus = summary?.sync.status ?? "never_synchronized";
  const isFailureState = syncStatus === "failed" || syncStatus === "timed_out";

  if (connectionLoading) {
    return <p className="text-sm text-muted-foreground">Loading FBA Inventory…</p>;
  }
  if (connectionError) {
    return <p className="text-sm text-destructive">{connectionError}</p>;
  }
  if (marketplaces.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No Amazon marketplace is connected yet. Connect a seller account to see FBA Inventory here.
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
              params.delete("invPage");
            })
          }
        />
        <button
          type="button"
          onClick={handleSync}
          disabled={triggering || inventorySyncShowsActiveSpinner(syncStatus)}
          className="inline-flex h-10 items-center gap-2 rounded-md border border-input bg-surface px-4 text-sm font-medium transition-colors hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-60"
        >
          {inventorySyncShowsActiveSpinner(syncStatus) || triggering ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Sync FBA Inventory
        </button>
      </div>

      {/* Persistent, always-visible FBA-only disclosure — this is a
          correctness-critical label, not decoration: this data has no
          visibility into merchant-fulfilled stock. */}
      <p className="rounded-md border border-border bg-surface-muted px-3 py-2 text-xs text-muted-foreground">
        Shows <span className="font-medium text-foreground">FBA-fulfilled inventory only</span>, from Amazon&apos;s
        own current snapshot. Merchant-fulfilled stock is not included and is not visible here.
      </p>

      <div
        className={cn(
          "rounded-md border px-4 py-3 text-sm",
          isFailureState ? "border-destructive/40 bg-destructive/5 text-destructive" : "border-border bg-surface-muted text-muted-foreground",
        )}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-medium text-foreground">{INVENTORY_SYNC_STATUS_LABEL[syncStatus]}</span>
          {syncStatus === "queued" && <span>· Waiting for a worker to pick this up</span>}
          {summary?.sync.last_successful_synchronized_at && (
            <span>· Last synchronized {formatDateTime(summary.sync.last_successful_synchronized_at)}</span>
          )}
        </div>
        {isFailureState && (
          <p className="mt-1 text-xs">{formatInventoryFailureReason(summary?.sync.failure_class ?? null)}</p>
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

      {summaryError && <p className="text-sm text-destructive">{summaryError}</p>}

      {summary && syncStatus !== "never_synchronized" && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <MetricCard label="Total SKUs" value={formatInventoryQuantity(summary.total)} />
          <MetricCard label="Active" value={formatInventoryQuantity(summary.active_count)} />
          <MetricCard label="Inactive" value={formatInventoryQuantity(summary.inactive_count)} />
          <MetricCard label="With ASIN" value={formatInventoryQuantity(summary.with_asin_count)} />
          <MetricCard label="Zero fulfillable" value={formatInventoryQuantity(summary.zero_fulfillable_count)} />
        </div>
      )}

      {syncStatus === "never_synchronized" && (
        <p className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
          No FBA Inventory data yet. Run a sync to import Amazon&apos;s current inventory summary for this
          marketplace.
        </p>
      )}

      <section className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Inventory</h2>
          <input
            type="search"
            value={search}
            onChange={(event) =>
              replaceParams((params) => {
                if (event.target.value) params.set("q", event.target.value);
                else params.delete("q");
                params.delete("invPage");
              })
            }
            placeholder="Search SKU, FNSKU, or ASIN"
            className="h-9 w-56 rounded-md border border-input bg-surface px-3 text-sm"
          />
        </div>

        {listError && <p className="text-sm text-destructive">{listError}</p>}
        {!listLoading && items.length === 0 && !listError && (
          <p className="rounded-md border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
            {syncStatus === "never_synchronized"
              ? "No inventory data yet. Run a sync to import FBA inventory for this marketplace."
              : "No inventory found for this view."}
          </p>
        )}
        {items.length > 0 && (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="bg-surface-muted text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Product</th>
                  <th className="px-3 py-2">Condition</th>
                  <th className="px-3 py-2">Total</th>
                  <th className="px-3 py-2">Fulfillable</th>
                  <th className="px-3 py-2">Reserved</th>
                  <th className="px-3 py-2">Inbound</th>
                  <th className="px-3 py-2">Unfulfillable</th>
                  <th className="px-3 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} className={cn("border-t border-border", !item.is_active && "opacity-60")}>
                    <td className="px-3 py-2">
                      <p className="font-medium">{item.product_name ?? item.seller_sku}</p>
                      <p className="text-xs text-muted-foreground">
                        SKU {item.seller_sku}
                        {item.asin ? ` · ASIN ${item.asin}` : ""}
                        {item.fnsku ? ` · FNSKU ${item.fnsku}` : ""}
                      </p>
                    </td>
                    <td className="px-3 py-2">{item.condition}</td>
                    <td className="px-3 py-2">{formatInventoryQuantity(item.total_quantity)}</td>
                    <td className="px-3 py-2">{formatInventoryQuantity(item.fulfillable_quantity)}</td>
                    <td className="px-3 py-2">{formatInventoryQuantity(item.reserved_total_quantity)}</td>
                    <td className="px-3 py-2">{formatInventoryQuantity(item.inbound_total_quantity)}</td>
                    <td className="px-3 py-2">{formatInventoryQuantity(item.unfulfillable_total_quantity)}</td>
                    <td className="px-3 py-2">
                      {item.is_active ? (
                        <span className="text-xs text-muted-foreground">Active</span>
                      ) : (
                        <span
                          className="inline-flex cursor-help items-center rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700"
                          title={`Not confirmed present in the latest complete sync (as of ${formatDateTime(item.last_seen_at)}) — quantities shown are last-known, not current.`}
                        >
                          Not confirmed
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {total > PAGE_SIZE && (
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>
              Page {page} of {totalPages}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => replaceParams((params) => params.set("invPage", String(page - 1)))}
                className="rounded-md border border-input px-3 py-1.5 disabled:opacity-40"
              >
                Previous
              </button>
              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => replaceParams((params) => params.set("invPage", String(page + 1)))}
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
