import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, Panel } from "@/components/ui/layout";
import { formatDate, formatPrice, formatProductType } from "@/lib/seller-listings-view";
import type { ListingCollectionItem } from "@/lib/types";

function StateBadge({ on, onLabel, offLabel }: { on: boolean; onLabel: string; offLabel: string }) {
  return (
    <Badge
      variant="outline"
      className={
        on
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
          : "text-muted-foreground"
      }
    >
      {on ? onLabel : offLabel}
    </Badge>
  );
}

function IssueBadge({ count, severity }: { count: number; severity: ListingCollectionItem["highest_issue_severity"] }) {
  if (count === 0) {
    return <span className="text-xs text-muted-foreground">No issues</span>;
  }
  const tone =
    severity === "ERROR"
      ? "border-destructive/30 bg-destructive/10 text-destructive"
      : severity === "WARNING"
        ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400"
        : "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-400";
  return (
    <Badge variant="outline" className={tone}>
      {count} {severity ? severity.toLowerCase() : "issue"}
      {count === 1 ? "" : "s"}
    </Badge>
  );
}

export function SellerListingsTable({
  items,
  total,
  offset,
  limit,
  loading,
  hasEverSynchronized,
  onPageChange,
  onOpenDetail,
}: {
  items: ListingCollectionItem[];
  total: number;
  offset: number;
  limit: number;
  loading: boolean;
  hasEverSynchronized: boolean;
  onPageChange: (nextOffset: number) => void;
  onOpenDetail: (listingId: string) => void;
}) {
  if (loading && items.length === 0) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading listings…
      </div>
    );
  }

  if (!loading && items.length === 0) {
    return (
      <EmptyState
        title={hasEverSynchronized ? "No listings match these filters." : "No listings yet."}
        description={
          hasEverSynchronized
            ? "Try clearing a filter or search term."
            : "This marketplace has not completed a Listings synchronization yet."
        }
      />
    );
  }

  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = Math.min(offset + items.length, total);

  return (
    <Panel className="overflow-x-auto">
      <table className="w-full min-w-[64rem] text-left text-sm">
        <thead className="border-b border-border text-xs text-muted-foreground">
          <tr>
            <th className="px-4 py-3 font-medium">Seller SKU</th>
            <th className="px-4 py-3 font-medium">ASIN</th>
            <th className="px-4 py-3 font-medium">Product type</th>
            <th className="px-4 py-3 font-medium">Active</th>
            <th className="px-4 py-3 font-medium">Buyable</th>
            <th className="px-4 py-3 font-medium">Discoverable</th>
            <th className="px-4 py-3 font-medium">Consumer price</th>
            <th className="px-4 py-3 font-medium">Issues</th>
            <th className="px-4 py-3 font-medium">Last seen</th>
            <th className="px-4 py-3 text-right font-medium">Details</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {items.map((item) => (
            <tr key={item.id} className="align-top">
              <td className="px-4 py-3 font-mono text-xs">{item.seller_sku}</td>
              <td className="px-4 py-3 font-mono text-xs">{item.asin ?? "—"}</td>
              <td className="px-4 py-3">{formatProductType(item.product_type)}</td>
              <td className="px-4 py-3">
                <StateBadge on={item.is_active} onLabel="Active" offLabel="Inactive" />
              </td>
              <td className="px-4 py-3">
                <StateBadge on={item.is_buyable} onLabel="Buyable" offLabel="Not buyable" />
              </td>
              <td className="px-4 py-3">
                <StateBadge on={item.is_discoverable} onLabel="Discoverable" offLabel="Not discoverable" />
              </td>
              <td className="px-4 py-3 tabular-nums">{formatPrice(item.price_amount, item.price_currency)}</td>
              <td className="px-4 py-3">
                <IssueBadge count={item.issue_count} severity={item.highest_issue_severity} />
              </td>
              <td className="px-4 py-3 whitespace-nowrap">{formatDate(item.last_seen_at)}</td>
              <td className="px-4 py-3 text-right whitespace-nowrap">
                <Button type="button" size="sm" variant="outline" onClick={() => onOpenDetail(item.id)}>
                  Details
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex items-center justify-between gap-3 border-t border-border px-4 py-3 text-sm text-muted-foreground">
        <p>
          {pageStart}–{pageEnd} of {total}
        </p>
        <div className="flex gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={offset <= 0 || loading}
            onClick={() => onPageChange(Math.max(offset - limit, 0))}
          >
            Previous
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={offset + limit >= total || loading}
            onClick={() => onPageChange(offset + limit)}
          >
            Next
          </Button>
        </div>
      </div>
    </Panel>
  );
}
