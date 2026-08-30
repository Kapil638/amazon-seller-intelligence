import { marketplaceDisplayName, marketplaceSubtitle } from "@/lib/seller-listings-view";
import type { AmazonSellerMarketplace } from "@/lib/types";

export function SellerListingsMarketplaceSelector({
  marketplaces,
  selectedId,
  onChange,
  disabled,
}: {
  marketplaces: AmazonSellerMarketplace[];
  selectedId: string | null;
  onChange: (participationId: string) => void;
  disabled?: boolean;
}) {
  const selected = marketplaces.find((m) => m.id === selectedId) ?? null;

  return (
    <div className="min-w-0">
      <label htmlFor="seller-listings-marketplace" className="text-xs font-medium text-muted-foreground">
        Marketplace
      </label>
      <select
        id="seller-listings-marketplace"
        className="mt-1 flex h-10 w-full min-w-[14rem] rounded-md border border-input bg-surface px-3 py-2 text-sm transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
        value={selectedId ?? ""}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {marketplaces
          .filter((m): m is AmazonSellerMarketplace & { id: string } => Boolean(m.id))
          .map((marketplace) => (
            <option key={marketplace.id} value={marketplace.id}>
              {marketplaceDisplayName(marketplace)}
              {marketplace.country_code ? ` (${marketplace.country_code})` : ""}
            </option>
          ))}
      </select>
      {selected ? (
        <p className="mt-1 text-xs text-muted-foreground">{marketplaceSubtitle(selected)}</p>
      ) : null}
    </div>
  );
}
