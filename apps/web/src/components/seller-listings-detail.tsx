"use client";

import { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/layout";
import { fetchListingDetail, ListingsApiError } from "@/lib/api";
import { formatDateTime, formatPrice, formatProductType, highestSeverityFirst } from "@/lib/seller-listings-view";
import type { ListingDetail } from "@/lib/types";

function severityBadgeClassName(severity: string): string {
  if (severity === "ERROR") {
    return "border-destructive/30 bg-destructive/10 text-destructive";
  }
  if (severity === "WARNING") {
    return "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400";
  }
  return "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-400";
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  );
}

export function SellerListingsDetail({
  participationId,
  listingId,
  refreshToken,
  onClose,
}: {
  participationId: string;
  listingId: string;
  /** Bump this (e.g. after a successful Sync listings) to force a
   * background refetch without ever clearing what's already on screen. */
  refreshToken?: number;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<ListingDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNotFound(false);
    // Deliberately does NOT clear `detail` here: a refresh (refreshToken
    // bump) must leave already-loaded content visible until the new data
    // arrives, never flash back to a bare loading state.
    fetchListingDetail(participationId, listingId)
      .then((result) => {
        if (!cancelled) setDetail(result);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ListingsApiError && err.kind === "not_found") {
          setNotFound(true);
        } else {
          setError("This listing could not be loaded. Please try again.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [participationId, listingId, refreshToken]);

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-labelledby="seller-listing-detail-title"
      onClick={onClose}
    >
      <div
        className="h-full w-full max-w-lg overflow-y-auto bg-surface shadow-[var(--shadow-lg)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="sticky top-0 flex items-center justify-between border-b border-border bg-surface px-5 py-4">
          <h2 id="seller-listing-detail-title" className="text-base font-semibold">
            Listing details
          </h2>
          <Button type="button" variant="ghost" size="icon" aria-label="Close" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="space-y-6 p-5">
          {loading && !detail ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading listing…
            </div>
          ) : null}

          {notFound ? (
            <p className="text-sm text-muted-foreground">
              This listing was not found for the selected marketplace.
            </p>
          ) : null}

          {error ? <p className="text-sm text-destructive">{error}</p> : null}

          {!notFound && !error && detail ? (
            <>
              <Panel className="p-4">
                <p className="text-base font-semibold leading-snug">{detail.item_name ?? detail.seller_sku}</p>
                <div className="mt-3 space-y-0.5 divide-y divide-border">
                  <Row label="Seller SKU" value={<span className="font-mono text-xs">{detail.seller_sku}</span>} />
                  <Row label="ASIN" value={<span className="font-mono text-xs">{detail.asin ?? "—"}</span>} />
                  <Row label="Product type" value={formatProductType(detail.product_type)} />
                  <Row label="Consumer price" value={formatPrice(detail.price_amount, detail.price_currency)} />
                </div>
              </Panel>

              <Panel className="p-4">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">State</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <Badge
                    variant="outline"
                    className={
                      detail.is_active
                        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                        : "text-muted-foreground"
                    }
                  >
                    {detail.is_active ? "Active" : "Inactive"}
                  </Badge>
                  <Badge
                    variant="outline"
                    className={
                      detail.is_buyable
                        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                        : "text-muted-foreground"
                    }
                  >
                    {detail.is_buyable ? "Buyable" : "Not buyable"}
                  </Badge>
                  <Badge
                    variant="outline"
                    className={
                      detail.is_discoverable
                        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                        : "text-muted-foreground"
                    }
                  >
                    {detail.is_discoverable ? "Discoverable" : "Not discoverable"}
                  </Badge>
                </div>
                {detail.status.length > 0 ? (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Amazon listing status: {detail.status.join(", ")}
                  </p>
                ) : null}
              </Panel>

              <Panel className="p-4">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Amazon-reported issues ({detail.issue_count})
                </p>
                {detail.issues.length === 0 ? (
                  <p className="mt-2 text-sm text-muted-foreground">No issues reported by Amazon.</p>
                ) : (
                  <ul className="mt-2 space-y-2">
                    {highestSeverityFirst(detail.issues).map((issue, index) => (
                      <li key={`${issue.code}-${index}`} className="rounded-md border border-border p-2.5">
                        <div className="flex items-center gap-2">
                          <Badge variant="outline" className={severityBadgeClassName(issue.severity)}>
                            {issue.severity}
                          </Badge>
                          <span className="font-mono text-xs text-muted-foreground">{issue.code}</span>
                        </div>
                        <p className="mt-1.5 text-sm leading-relaxed">{issue.message}</p>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>

              <Panel className="p-4">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Fulfillment availability
                </p>
                {detail.fulfillment_availability.length === 0 ? (
                  <p className="mt-2 text-sm text-muted-foreground">No fulfillment availability reported.</p>
                ) : (
                  <ul className="mt-2 space-y-1">
                    {detail.fulfillment_availability.map((entry, index) => (
                      <li key={index} className="flex items-center justify-between text-sm">
                        <span>{entry.fulfillmentChannelCode}</span>
                        <span className="tabular-nums text-muted-foreground">
                          {entry.quantity != null ? entry.quantity : "—"}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>

              <Panel className="p-4">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Product types</p>
                {detail.product_types.length === 0 ? (
                  <p className="mt-2 text-sm text-muted-foreground">Not reported by Amazon.</p>
                ) : (
                  <ul className="mt-2 space-y-1 text-sm">
                    {detail.product_types.map((entry, index) => (
                      <li key={index}>{formatProductType(entry.productType)}</li>
                    ))}
                  </ul>
                )}
              </Panel>

              <Panel className="p-4">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Provenance</p>
                <div className="mt-2 space-y-0.5 divide-y divide-border">
                  <Row label="First seen" value={formatDateTime(detail.first_seen_at)} />
                  <Row label="Last seen" value={formatDateTime(detail.last_seen_at)} />
                  <Row
                    label="Last successful synchronization"
                    value={formatDateTime(detail.last_successful_sync_at)}
                  />
                </div>
              </Panel>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
