"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { SellerListingsMarketplaceSelector } from "@/components/seller-listings-marketplace-selector";
import {
  AmazonConnectionError,
  fetchAmazonConnection,
  fetchListingsSummary,
  fetchOrdersSummary,
} from "@/lib/api";
import { CANONICAL_MARKETPLACE_ID, formatDateTime } from "@/lib/seller-listings-view";
import { ORDERS_SYNC_STATUS_LABEL } from "@/lib/seller-orders-view";
import type { AmazonSellerMarketplace, ListingsSummary, OrdersSummary } from "@/lib/types";

type AddressableMarketplace = AmazonSellerMarketplace & { id: string };

/**
 * 12B.4D Seller Overview. Built only from data the Listings and Orders
 * read APIs can actually provide — no invented metric, no cross-dataset
 * join beyond what these two summary endpoints already support. A true
 * per-SKU cross-dataset view (e.g. "listings with issues AND orders")
 * needs joined row-level data neither summary endpoint exposes; deferred
 * to a future increment (see docs/AI_HANDOVER/12B4D_ORDERS_INGESTION_AND_UI.md).
 */
export function SellerOverview() {
  const searchParams = useSearchParams();
  const [marketplaces, setMarketplaces] = useState<AddressableMarketplace[]>([]);
  const [participationId, setParticipationId] = useState<string | null>(searchParams.get("participation"));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [listingsSummary, setListingsSummary] = useState<ListingsSummary | null>(null);
  const [ordersSummary, setOrdersSummary] = useState<OrdersSummary | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchAmazonConnection()
      .then((overview) => {
        if (cancelled) return;
        const addressable = (overview.marketplaces ?? []).filter((m): m is AddressableMarketplace => Boolean(m.id));
        setMarketplaces(addressable);
        if (!participationId && addressable.length > 0) {
          const canonical = addressable.find((m) => m.marketplace_id === CANONICAL_MARKETPLACE_ID);
          setParticipationId((canonical ?? addressable[0]).id);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(err instanceof AmazonConnectionError ? err.message : "Amazon Connection could not be reached.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!participationId) return;
    let cancelled = false;
    Promise.allSettled([fetchListingsSummary(participationId), fetchOrdersSummary(participationId)]).then(
      ([listingsResult, ordersResult]) => {
        if (cancelled) return;
        setListingsSummary(listingsResult.status === "fulfilled" ? listingsResult.value : null);
        setOrdersSummary(ordersResult.status === "fulfilled" ? ordersResult.value : null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [participationId]);

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading Seller Overview…</p>;
  }
  if (error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }
  if (marketplaces.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No Amazon marketplace is connected yet.{" "}
        <Link href="/connection" className="underline">
          Connect a seller account
        </Link>{" "}
        to see an overview here.
      </p>
    );
  }

  const attentionItems: string[] = [];
  if (listingsSummary && listingsSummary.with_issues_count > 0) {
    attentionItems.push(`${listingsSummary.with_issues_count} listing${listingsSummary.with_issues_count === 1 ? "" : "s"} need attention`);
  }
  if (ordersSummary && ordersSummary.cancelled_count > 0) {
    attentionItems.push(`${ordersSummary.cancelled_count} cancelled order${ordersSummary.cancelled_count === 1 ? "" : "s"}`);
  }
  if (listingsSummary && listingsSummary.sync.status === "never_synchronized") {
    attentionItems.push("Listings has never been synchronized");
  }
  if (ordersSummary && ordersSummary.sync.status === "never_synchronized") {
    attentionItems.push("Orders has never been synchronized");
  }
  const incompleteData =
    (ordersSummary && ordersSummary.sync.pagination_complete === false) ||
    (listingsSummary && listingsSummary.sync.status === "running");

  return (
    <div className="flex flex-col gap-6">
      <SellerListingsMarketplaceSelector
        marketplaces={marketplaces}
        selectedId={participationId}
        onChange={setParticipationId}
      />

      {incompleteData && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-4 py-2.5 text-sm text-amber-700 dark:text-amber-400">
          Data is still being imported — figures below may be incomplete until synchronization finishes.
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card label="Listings" value={listingsSummary ? listingsSummary.total_listings.toLocaleString() : "—"} href="/seller/listings" />
        <Card
          label="Needs attention"
          value={listingsSummary ? listingsSummary.with_issues_count.toLocaleString() : "—"}
          href="/seller/listings"
        />
        <Card label="Orders" value={ordersSummary ? ordersSummary.total_orders.toLocaleString() : "—"} href="/seller/orders" />
        <Card label="Cancelled orders" value={ordersSummary ? ordersSummary.cancelled_count.toLocaleString() : "—"} href="/seller/orders" />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <SyncCard
          title="Listings synchronization"
          statusLabel={listingsSummary ? listingsSummary.sync.status.replace(/_/g, " ") : "Unknown"}
          lastSuccess={listingsSummary?.sync.last_successful_synchronized_at ?? null}
          href="/seller/listings"
        />
        <SyncCard
          title="Orders synchronization"
          statusLabel={ordersSummary ? ORDERS_SYNC_STATUS_LABEL[ordersSummary.sync.status] : "Unknown"}
          lastSuccess={ordersSummary?.sync.last_successful_synchronized_at ?? null}
          href="/seller/orders"
        />
      </div>

      {attentionItems.length > 0 && (
        <div className="rounded-md border border-border p-4">
          <p className="mb-2 text-sm font-medium">Attention</p>
          <ul className="flex flex-col gap-1 text-sm text-muted-foreground">
            {attentionItems.map((item) => (
              <li key={item}>• {item}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Card({ label, value, href }: { label: string; value: string; href: string }) {
  return (
    <Link href={href} className="rounded-md border border-border bg-surface p-3 transition-colors hover:bg-surface-muted">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold">{value}</p>
    </Link>
  );
}

function SyncCard({
  title,
  statusLabel,
  lastSuccess,
  href,
}: {
  title: string;
  statusLabel: string;
  lastSuccess: string | null;
  href: string;
}) {
  return (
    <Link href={href} className="rounded-md border border-border bg-surface p-4 transition-colors hover:bg-surface-muted">
      <p className="text-sm font-medium">{title}</p>
      <p className="mt-1 text-sm text-muted-foreground capitalize">{statusLabel}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        {lastSuccess ? `Last successful sync: ${formatDateTime(lastSuccess)}` : "Never successfully synchronized"}
      </p>
    </Link>
  );
}
