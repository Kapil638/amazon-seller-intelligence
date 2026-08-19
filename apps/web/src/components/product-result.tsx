import type { ReactNode } from "react";

import { ProductMediaGallery } from "@/components/product-media-gallery";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Product, ProductSource } from "@/lib/types";

const SOURCE_LABELS: Record<ProductSource, string> = {
  mock: "Mock Data",
  manual: "Manual Input",
  amazon_public: "Amazon.in (public)",
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

function formatReviews(count: number): string {
  return new Intl.NumberFormat("en-IN").format(count);
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <div className="text-sm font-medium text-foreground">{children}</div>
    </div>
  );
}

function available(value: ReactNode | null | undefined, fallback = "Not available"): ReactNode {
  if (value == null || value === "") {
    return <span className="font-normal text-muted-foreground">{fallback}</span>;
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
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {source ? (
              <Badge variant="secondary">{SOURCE_LABELS[source]}</Badge>
            ) : null}
            {product.brand ? <Badge variant="outline">{product.brand}</Badge> : null}
            {product.availability ? (
              <Badge variant="outline">{product.availability}</Badge>
            ) : null}
            <Badge variant="outline" className="font-mono">
              {product.asin}
            </Badge>
          </div>
          <CardTitle className="text-2xl leading-snug">
            {product.title.trim() ? product.title : "Not available"}
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-6 sm:grid-cols-2">
          <Stat label="Brand">{available(product.brand)}</Stat>
          <Stat label="Price">
            {product.price
              ? formatPrice(product.price.amount, product.price.currency)
              : available(null)}
          </Stat>
          <Stat label="Rating">
            {product.rating != null ? `${product.rating.toFixed(1)} ★` : available(null)}
          </Stat>
          <Stat label="Review count">
            {product.review_count != null
              ? formatReviews(product.review_count)
              : available(null)}
          </Stat>
          <Stat label="Category">{available(product.category)}</Stat>
          <Stat label="BSR">
            {product.bsr
              ? `#${formatReviews(product.bsr.rank)} in ${product.bsr.category}`
              : available(null)}
          </Stat>
          <Stat label="Availability">{available(product.availability)}</Stat>
          <Stat label="Seller">{available(product.seller?.name)}</Stat>
          <Stat label="Marketplace">{product.marketplace}</Stat>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Description</CardTitle>
        </CardHeader>
        <CardContent>
          {product.description ? (
            <p className="text-sm leading-6 text-foreground">{product.description}</p>
          ) : (
            <p className="text-sm text-muted-foreground">Not available</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Bullet Points</CardTitle>
        </CardHeader>
        <CardContent>
          {product.bullet_points.length > 0 ? (
            <ul className="list-disc space-y-2 pl-5 text-sm leading-6 text-foreground">
              {product.bullet_points.map((point) => (
                <li key={point}>{point}</li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">Not available</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Product Media</CardTitle>
        </CardHeader>
        <CardContent>
          <ProductMediaGallery product={product} />
        </CardContent>
      </Card>
    </div>
  );
}
