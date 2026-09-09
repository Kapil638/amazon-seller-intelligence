"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";

import { InventoryApiError, fetchInventoryHealth, fetchInventoryHealthEvidence, fetchInventoryHealthSummary } from "@/lib/api";
import { formatDateTime } from "@/lib/seller-listings-view";
import {
  INVENTORY_HEALTH_ELIGIBILITY_LABEL,
  INVENTORY_HEALTH_FRESHNESS_LABEL,
  INVENTORY_HEALTH_OVERLAY_LABEL,
  INVENTORY_HEALTH_STATES,
  INVENTORY_HEALTH_STATE_LABEL,
  formatInventoryHealthDays,
  formatInventoryHealthQuantity,
  formatInventoryHealthVelocity,
  inventoryHealthStateBadgeTone,
} from "@/lib/seller-inventory-health-view";
import type { InventoryHealthEvidence, InventoryHealthInventoryState, InventoryHealthRow, InventoryHealthSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

const PAGE_SIZE = 25;

const TONE_CLASS: Record<"danger" | "warning" | "success" | "neutral", string> = {
  danger: "bg-red-500/10 text-red-700",
  warning: "bg-amber-500/10 text-amber-700",
  success: "bg-emerald-500/10 text-emerald-700",
  neutral: "bg-muted text-muted-foreground",
};

function StateBadge({ state }: { state: InventoryHealthInventoryState }) {
  const tone = inventoryHealthStateBadgeTone(state);
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", TONE_CLASS[tone])}>
      {INVENTORY_HEALTH_STATE_LABEL[state]}
    </span>
  );
}

/**
 * 12B.6C — Inventory Health and Replenishment Insights. Deterministic,
 * computed-on-read (no snapshot table, no Amazon call, no AI call) —
 * mounted as an additional section inside the existing FBA Inventory
 * page, never a new top-level nav tab (matches this codebase's own
 * `SellerLocalNav` precedent of keeping domain sub-views together).
 *
 * Every count/metric below distinguishes a real `0` from an
 * unavailable/ineligible `null` — this component must never render
 * `null` as if it were `0`, and must never present an unavailable
 * calculation (e.g. zero recent demand) as an infinite or fabricated
 * number.
 */
export function SellerInventoryHealth({ participationId }: { participationId: string }) {
  const [summary, setSummary] = useState<InventoryHealthSummary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [rows, setRows] = useState<InventoryHealthRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState<InventoryHealthInventoryState | null>(null);
  const [offset, setOffset] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<InventoryHealthEvidence | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      fetchInventoryHealthSummary(participationId),
      fetchInventoryHealth(participationId, {
        q: search || undefined,
        offset,
        limit: PAGE_SIZE,
        sortBy: "fulfillable_quantity",
        sortDir: "asc",
      }),
    ])
      .then(([summaryResult, listResult]) => {
        setSummary(summaryResult);
        setSummaryError(null);
        // State filtering happens client-side against the fetched page —
        // the approved V1 API design has no server-side state filter
        // (state is a computed field, not a stored column), so this is a
        // deliberate, documented scope boundary, not an oversight.
        const filtered = stateFilter ? listResult.items.filter((item) => item.inventory_state === stateFilter) : listResult.items;
        setRows(filtered);
        setTotal(listResult.total);
        setListError(null);
      })
      .catch((err: unknown) => {
        const message = err instanceof InventoryApiError ? err.message : "Inventory Health could not be loaded.";
        setSummaryError(message);
        setListError(message);
      })
      .finally(() => setLoading(false));
  }, [participationId, search, offset, stateFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const openEvidence = useCallback(
    (row: InventoryHealthRow) => {
      setExpandedId(row.inventory_id);
      setEvidence(null);
      setEvidenceLoading(true);
      fetchInventoryHealthEvidence(participationId, row.inventory_id)
        .then(setEvidence)
        .catch(() => setEvidence(null))
        .finally(() => setEvidenceLoading(false));
    },
    [participationId],
  );

  return (
    <section className="flex flex-col gap-4 border-t border-border pt-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold">Inventory Health</h2>
          <p className="text-xs text-muted-foreground">
            FBA-only — based on fulfillable stock and recent Sales &amp; Traffic demand. Not a supplier lead-time or
            reorder recommendation.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="inline-flex h-9 items-center gap-2 rounded-md border border-input bg-surface px-3 text-sm font-medium hover:bg-surface-muted disabled:opacity-60"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </button>
      </div>

      {(summaryError || listError) && <p className="text-sm text-red-600">{summaryError || listError}</p>}

      {summary && (
        <>
          <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
            <span>
              Inventory: {summary.inventory_sync.status === "never_synchronized" ? "Never synchronized" : "Synced"}
              {summary.inventory_sync.last_successful_synchronized_at &&
                ` — last successful ${formatDateTime(summary.inventory_sync.last_successful_synchronized_at)}`}
            </span>
            <span aria-hidden="true">·</span>
            <span>
              Sales &amp; Traffic: {summary.sales_traffic_sync.status === "never_synchronized" ? "Never synchronized" : "Synced"}
              {summary.sales_traffic_sync.last_successful_synchronized_at &&
                ` — last successful ${formatDateTime(summary.sales_traffic_sync.last_successful_synchronized_at)}`}
            </span>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                setStateFilter(null);
                setOffset(0);
              }}
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                stateFilter === null ? "border-primary bg-primary/10" : "border-border hover:bg-surface-muted",
              )}
            >
              All ({summary.total})
            </button>
            {INVENTORY_HEALTH_STATES.map((state) => (
              <button
                key={state}
                type="button"
                data-testid={`state-filter-${state}`}
                onClick={() => {
                  setStateFilter(state);
                  setOffset(0);
                }}
                className={cn(
                  "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                  stateFilter === state ? "border-primary bg-primary/10" : "border-border hover:bg-surface-muted",
                )}
              >
                {INVENTORY_HEALTH_STATE_LABEL[state]} ({summary.counts_by_inventory_state[state] ?? 0})
              </button>
            ))}
          </div>
        </>
      )}

      <input
        type="search"
        value={search}
        onChange={(e) => {
          setSearch(e.target.value);
          setOffset(0);
        }}
        placeholder="Search seller SKU, FNSKU, or ASIN"
        className="h-9 w-full max-w-sm rounded-md border border-input bg-surface px-3 text-sm"
      />

      {loading && rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">Loading Inventory Health…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No inventory rows match this filter.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-muted text-xs uppercase text-muted-foreground">
              <tr>
                <th className="px-3 py-2">Product</th>
                <th className="px-3 py-2">Fulfillable</th>
                <th className="px-3 py-2">Velocity</th>
                <th className="px-3 py-2">Days of cover</th>
                <th className="px-3 py-2">Potential (incl. inbound)</th>
                <th className="px-3 py-2">Freshness</th>
                <th className="px-3 py-2">State</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row) => (
                <Fragment key={row.inventory_id}>
                  <tr className="align-top" data-testid={`inventory-health-row-${row.inventory_id}`}>
                    <td className="px-3 py-2">
                      <p className="font-medium">{row.product_name ?? row.seller_sku}</p>
                      <p className="text-xs text-muted-foreground">
                        SKU {row.seller_sku} · {row.condition}
                        {row.asin && ` · ${row.asin}`}
                      </p>
                      {row.demand_eligibility !== "eligible" && (
                        <p className="text-xs text-amber-700">{INVENTORY_HEALTH_ELIGIBILITY_LABEL[row.demand_eligibility]}</p>
                      )}
                    </td>
                    <td className="px-3 py-2">{formatInventoryHealthQuantity(row.fulfillable_quantity)}</td>
                    <td className="px-3 py-2">{formatInventoryHealthVelocity(row.units_per_covered_day)}</td>
                    <td className="px-3 py-2">{formatInventoryHealthDays(row.fulfillable_days_of_cover)}</td>
                    <td className="px-3 py-2">
                      {formatInventoryHealthQuantity(row.potential_units)}
                      {row.potential_units_incomplete_inputs && (
                        <span className="ml-1 text-xs text-amber-700" title="Some inbound quantities were not reported by Amazon">
                          (partial)
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">{INVENTORY_HEALTH_FRESHNESS_LABEL[row.freshness_state]}</td>
                    <td className="px-3 py-2">
                      <StateBadge state={row.inventory_state} />
                      {row.overlays.length > 0 && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {row.overlays.map((overlay) => (
                            <span key={overlay} className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                              {INVENTORY_HEALTH_OVERLAY_LABEL[overlay]}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        onClick={() => (expandedId === row.inventory_id ? setExpandedId(null) : openEvidence(row))}
                        className="text-xs font-medium text-primary hover:underline"
                      >
                        {expandedId === row.inventory_id ? "Hide evidence" : "Evidence"}
                      </button>
                    </td>
                  </tr>
                  {expandedId === row.inventory_id && (
                    <tr>
                      <td colSpan={8} className="bg-surface-muted px-3 py-3 text-xs">
                        {evidenceLoading ? (
                          <span className="text-muted-foreground">Loading evidence…</span>
                        ) : evidence ? (
                          <dl className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
                            <div>
                              <dt className="text-muted-foreground">Formula version</dt>
                              <dd>{evidence.formula_version}</dd>
                            </div>
                            <div>
                              <dt className="text-muted-foreground">Thresholds</dt>
                              <dd>
                                Low &lt; {evidence.thresholds.low_coverage_days_threshold}d, High &gt;{" "}
                                {evidence.thresholds.high_coverage_days_threshold}d
                              </dd>
                            </div>
                            <div>
                              <dt className="text-muted-foreground">Preferred / min window</dt>
                              <dd>
                                {evidence.thresholds.preferred_window_days}d / {evidence.thresholds.min_eligible_window_days}d
                              </dd>
                            </div>
                            <div>
                              <dt className="text-muted-foreground">Sales window</dt>
                              <dd>
                                {evidence.sales_window_start ?? "—"} → {evidence.sales_window_end ?? "—"} (
                                {evidence.sales_covered_days ?? "—"} days)
                              </dd>
                            </div>
                            <div>
                              <dt className="text-muted-foreground">Inventory observed at</dt>
                              <dd>{evidence.inventory_observed_at ? formatDateTime(evidence.inventory_observed_at) : "—"}</dd>
                            </div>
                            <div>
                              <dt className="text-muted-foreground">Amazon last updated time</dt>
                              <dd>{evidence.amazon_last_updated_time ? formatDateTime(evidence.amazon_last_updated_time) : "Not reported by Amazon"}</dd>
                            </div>
                            <div>
                              <dt className="text-muted-foreground">Sales ingestion completed</dt>
                              <dd>{evidence.sales_traffic_ingestion_completed_at ? formatDateTime(evidence.sales_traffic_ingestion_completed_at) : "—"}</dd>
                            </div>
                            <div>
                              <dt className="text-muted-foreground">Inventory run id</dt>
                              <dd className="truncate">{evidence.inventory_ingestion_run_id ?? "—"}</dd>
                            </div>
                            <div>
                              <dt className="text-muted-foreground">Sales run id</dt>
                              <dd className="truncate">{evidence.sales_traffic_ingestion_run_id ?? "—"}</dd>
                            </div>
                          </dl>
                        ) : (
                          <span className="text-muted-foreground">Evidence could not be loaded.</span>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={offset <= 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              className="rounded-md border border-input px-3 py-1.5 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
              className="rounded-md border border-input px-3 py-1.5 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
