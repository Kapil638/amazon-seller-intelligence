import type { ReactNode } from "react";

import { ProductMediaGallery } from "@/components/product-media-gallery";
import { Kpi, Panel, Section } from "@/components/ui/layout";
import type { Product, ProductSource } from "@/lib/types";

const SOURCE_LABELS: Record<ProductSource, string> = {
  mock: "Mock catalog",
  manual: "Manual entry",
  amazon_public: "Amazon.in public",
  rainforest: "Rainforest",
};

function formatPrice(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${currency} ${amount}`;
  }
}

function formatNumber(count: number): string {
  return new Intl.NumberFormat("en-IN").format(count);
}

function formatFetched(value: string): string | null {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function available(value: ReactNode | null | undefined): ReactNode {
  if (value == null || value === "") {
    return <span className="font-normal text-muted-foreground">Not available</span>;
  }
  return value;
}

export function ProductResult({
  product,
  source,
}: {
  product: Product;
  source?: ProductSource;
}) {
  const fetched = formatFetched(product.last_fetched_at);
  const image = product.images[0];

  return (
    <Section title="Product overview">
      <Panel className="overflow-hidden">
        <div className="grid gap-6 p-5 lg:grid-cols-[13rem_1fr] lg:gap-8">
          <div className="flex h-52 items-center justify-center overflow-hidden rounded-md bg-surface-subtle lg:h-56">
            {image ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={image.url} alt={image.alt ?? product.title} className="max-h-full max-w-full object-contain" />
            ) : (
              <span className="text-sm text-muted-foreground">No image</span>
            )}
          </div>
          <div className="min-w-0 space-y-5">
            <div className="space-y-1.5">
              <h3 className="text-lg font-semibold leading-snug tracking-tight">
                {product.title.trim() ? product.title : "Not available"}
              </h3>
              <p className="text-sm text-muted-foreground">{product.brand || "Brand not available"}</p>
            </div>
            <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
              <Kpi label="ASIN" value={<span className="font-mono text-base">{product.asin}</span>} />
              <Kpi
                label="Price"
                value={
                  product.price
                    ? formatPrice(product.price.amount, product.price.currency)
                    : available(null)
                }
              />
              <Kpi
                label="Rating"
                value={product.rating != null ? `${product.rating.toFixed(1)} ★` : available(null)}
              />
              <Kpi
                label="Reviews"
                value={product.review_count != null ? formatNumber(product.review_count) : available(null)}
              />
              <Kpi
                label="BSR"
                value={
                  product.bsr
                    ? `#${formatNumber(product.bsr.rank)}`
                    : available(null)
                }
                hint={product.bsr?.category ?? undefined}
              />
              <Kpi label="Availability" value={available(product.availability)} />
            </div>
            <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
              <span className="rounded-md border border-border px-2 py-0.5">{product.marketplace}</span>
              {source ? (
                <span className="rounded-md border border-border px-2 py-0.5">{SOURCE_LABELS[source]}</span>
              ) : null}
              {fetched ? (
                <span className="rounded-md border border-border px-2 py-0.5">Fetched {fetched}</span>
              ) : null}
              {product.seller?.name ? (
                <span className="rounded-md border border-border px-2 py-0.5">{product.seller.name}</span>
              ) : null}
            </div>
          </div>
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel className="p-5">
          <h3 className="mb-3 text-[0.95rem] font-semibold">Description</h3>
          {product.description ? (
            <p className="text-sm leading-6 text-foreground">{product.description}</p>
          ) : (
            <p className="text-sm text-muted-foreground">Not available</p>
          )}
        </Panel>
        <Panel className="p-5">
          <h3 className="mb-3 text-[0.95rem] font-semibold">Bullet points</h3>
          {product.bullet_points.length > 0 ? (
            <ul className="list-disc space-y-1.5 pl-5 text-sm leading-6">
              {product.bullet_points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">Not available</p>
          )}
        </Panel>
      </div>

      <Panel className="p-5">
        <h3 className="mb-4 text-[0.95rem] font-semibold">Product media</h3>
        <ProductMediaGallery product={product} />
      </Panel>
    </Section>
  );
}
