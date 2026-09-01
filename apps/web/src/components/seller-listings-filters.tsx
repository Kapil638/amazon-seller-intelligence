"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { ListingIssueSeverity, ListingSortField, SortDirection } from "@/lib/types";

const SELECT_CLASS =
  "flex h-9 rounded-md border border-input bg-surface px-2.5 text-sm transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export type SellerListingsFilterState = {
  q: string;
  isActive: boolean | undefined;
  isBuyable: boolean | undefined;
  isDiscoverable: boolean | undefined;
  hasIssues: boolean | undefined;
  highestIssueSeverity: ListingIssueSeverity | undefined;
  productType: string;
  sortBy: ListingSortField;
  sortDir: SortDirection;
};

export const DEFAULT_FILTER_STATE: SellerListingsFilterState = {
  q: "",
  isActive: undefined,
  isBuyable: undefined,
  isDiscoverable: undefined,
  hasIssues: undefined,
  highestIssueSeverity: undefined,
  productType: "",
  sortBy: "last_seen_at",
  sortDir: "desc",
};

function tribool(value: string): boolean | undefined {
  if (value === "true") return true;
  if (value === "false") return false;
  return undefined;
}

export function SellerListingsFilters({
  filters,
  onChange,
  onReset,
}: {
  filters: SellerListingsFilterState;
  onChange: (next: Partial<SellerListingsFilterState>) => void;
  onReset: () => void;
}) {
  const [searchInput, setSearchInput] = useState(filters.q);

  // Keep the visible input in sync when filters are reset/changed
  // externally (e.g. the Reset button, or a marketplace change).
  useEffect(() => {
    setSearchInput(filters.q);
  }, [filters.q]);

  // Debounced search: the URL/query state (and the API request it drives)
  // only updates 300ms after the user stops typing.
  useEffect(() => {
    if (searchInput === filters.q) {
      return;
    }
    const timeout = setTimeout(() => {
      onChange({ q: searchInput });
    }, 300);
    return () => clearTimeout(timeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput]);

  const isDefault =
    filters.q === "" &&
    filters.isActive === undefined &&
    filters.isBuyable === undefined &&
    filters.isDiscoverable === undefined &&
    filters.hasIssues === undefined &&
    filters.highestIssueSeverity === undefined &&
    filters.productType === "" &&
    filters.sortBy === DEFAULT_FILTER_STATE.sortBy &&
    filters.sortDir === DEFAULT_FILTER_STATE.sortDir;

  return (
    <div className="flex flex-wrap items-end gap-3">
      <div className="min-w-[14rem] flex-1">
        <label htmlFor="seller-listings-search" className="text-xs font-medium text-muted-foreground">
          Search SKU or ASIN
        </label>
        <Input
          id="seller-listings-search"
          className="mt-1"
          placeholder="e.g. SKU-1024 or B0EXAMPLE1"
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
        />
      </div>

      <div>
        <label htmlFor="seller-listings-active" className="text-xs font-medium text-muted-foreground">
          Active
        </label>
        <select
          id="seller-listings-active"
          className={`mt-1 ${SELECT_CLASS}`}
          value={filters.isActive === undefined ? "" : String(filters.isActive)}
          onChange={(event) => onChange({ isActive: tribool(event.target.value) })}
        >
          <option value="">Any</option>
          <option value="true">Active</option>
          <option value="false">Inactive</option>
        </select>
      </div>

      <div>
        <label htmlFor="seller-listings-buyable" className="text-xs font-medium text-muted-foreground">
          Buyable
        </label>
        <select
          id="seller-listings-buyable"
          className={`mt-1 ${SELECT_CLASS}`}
          value={filters.isBuyable === undefined ? "" : String(filters.isBuyable)}
          onChange={(event) => onChange({ isBuyable: tribool(event.target.value) })}
        >
          <option value="">Any</option>
          <option value="true">Buyable</option>
          <option value="false">Not buyable</option>
        </select>
      </div>

      <div>
        <label htmlFor="seller-listings-discoverable" className="text-xs font-medium text-muted-foreground">
          Discoverable
        </label>
        <select
          id="seller-listings-discoverable"
          className={`mt-1 ${SELECT_CLASS}`}
          value={filters.isDiscoverable === undefined ? "" : String(filters.isDiscoverable)}
          onChange={(event) => onChange({ isDiscoverable: tribool(event.target.value) })}
        >
          <option value="">Any</option>
          <option value="true">Discoverable</option>
          <option value="false">Not discoverable</option>
        </select>
      </div>

      <div>
        <label htmlFor="seller-listings-issues" className="text-xs font-medium text-muted-foreground">
          Issues
        </label>
        <select
          id="seller-listings-issues"
          className={`mt-1 ${SELECT_CLASS}`}
          value={filters.hasIssues === undefined ? "" : String(filters.hasIssues)}
          onChange={(event) => onChange({ hasIssues: tribool(event.target.value) })}
        >
          <option value="">Any</option>
          <option value="true">Has issues</option>
          <option value="false">No issues</option>
        </select>
      </div>

      <div>
        <label htmlFor="seller-listings-severity" className="text-xs font-medium text-muted-foreground">
          Highest severity
        </label>
        <select
          id="seller-listings-severity"
          className={`mt-1 ${SELECT_CLASS}`}
          value={filters.highestIssueSeverity ?? ""}
          onChange={(event) =>
            onChange({
              highestIssueSeverity: (event.target.value || undefined) as ListingIssueSeverity | undefined,
            })
          }
        >
          <option value="">Any</option>
          <option value="ERROR">Error</option>
          <option value="WARNING">Warning</option>
          <option value="INFO">Info</option>
        </select>
      </div>

      <div className="w-36">
        <label htmlFor="seller-listings-product-type" className="text-xs font-medium text-muted-foreground">
          Product type
        </label>
        <Input
          id="seller-listings-product-type"
          className="mt-1"
          placeholder="e.g. TOY"
          value={filters.productType}
          onChange={(event) => onChange({ productType: event.target.value })}
        />
      </div>

      <div>
        <label htmlFor="seller-listings-sort" className="text-xs font-medium text-muted-foreground">
          Sort by
        </label>
        <select
          id="seller-listings-sort"
          className={`mt-1 ${SELECT_CLASS}`}
          value={filters.sortBy}
          onChange={(event) => onChange({ sortBy: event.target.value as ListingSortField })}
        >
          <option value="last_seen_at">Last seen</option>
          <option value="first_seen_at">First seen</option>
          <option value="seller_sku">Seller SKU</option>
          <option value="asin">ASIN</option>
          <option value="issue_count">Issue count</option>
          <option value="price_amount">Price</option>
        </select>
      </div>

      <div>
        <label htmlFor="seller-listings-sort-dir" className="text-xs font-medium text-muted-foreground">
          Direction
        </label>
        <select
          id="seller-listings-sort-dir"
          className={`mt-1 ${SELECT_CLASS}`}
          value={filters.sortDir}
          onChange={(event) => onChange({ sortDir: event.target.value as SortDirection })}
        >
          <option value="desc">Descending</option>
          <option value="asc">Ascending</option>
        </select>
      </div>

      <Button type="button" variant="outline" size="sm" disabled={isDefault} onClick={onReset}>
        Reset filters
      </Button>
    </div>
  );
}
