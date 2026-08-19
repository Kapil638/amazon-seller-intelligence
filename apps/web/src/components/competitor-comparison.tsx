"use client";

import { Panel, Section } from "@/components/ui/layout";
import { SeverityDot, SeverityLabel } from "@/components/ui/score";
import { cn } from "@/lib/utils";
import type { ComparisonMetric, CompetitorComparisonResponse } from "@/lib/types";

function formatValue(value: unknown, key: string): string {
  if (value == null || value === "") {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "Yes" : "No";
  }
  if (typeof value === "number") {
    if (key === "price") {
      return new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: "INR",
        maximumFractionDigits: 0,
      }).format(value);
    }
    if (key === "review_count" || key === "bsr") {
      return new Intl.NumberFormat("en-IN").format(value);
    }
    if (key === "rating") {
      return value.toFixed(1);
    }
    return String(value);
  }
  return String(value);
}

const NUMERIC_KEYS = new Set([
  "price",
  "rating",
  "review_count",
  "bsr",
  "image_count",
  "bullet_count",
  "overall_score",
  "title_score",
  "listing_score",
]);

function CompetitorLabel({ asin, index }: { asin: string; index: number }) {
  const letter = String.fromCharCode(65 + index);
  return (
    <div>
      <p>Competitor {letter}</p>
      <p className="font-mono text-[11px] font-normal text-muted-foreground">{asin}</p>
    </div>
  );
}

function MetricRow({
  metric,
  competitorAsins,
  highlight,
}: {
  metric: ComparisonMetric;
  competitorAsins: string[];
  highlight: boolean;
}) {
  const numeric = NUMERIC_KEYS.has(metric.key);
  return (
    <tr className={cn(highlight && "bg-surface-subtle/80")}>
      <th className="px-3 py-2.5 text-left text-sm font-medium">{metric.label}</th>
      <td className={cn("px-3 py-2.5 text-sm", numeric && "num")}>
        {formatValue(metric.target_value, metric.key)}
      </td>
      {competitorAsins.map((asin) => (
        <td key={`${metric.key}-${asin}`} className={cn("px-3 py-2.5 text-sm", numeric && "num")}>
          {formatValue(metric.competitor_values[asin], metric.key)}
        </td>
      ))}
    </tr>
  );
}

export function CompetitorComparisonView({
  comparison,
}: {
  comparison: CompetitorComparisonResponse;
}) {
  const competitorAsins = comparison.competitors.map((item) => item.product.asin);
  const summary = comparison.comparison.summary;
  const failed = comparison.failed_competitors;
  const highlighted = new Set(
    comparison.comparison.gaps.filter((gap) => gap.severity === "high").map((gap) => gap.dimension),
  );

  return (
    <Section
      title="Competitor comparison"
      description="Observed catalog and listing metrics. A higher or lower number is not automatically better. Lower price is not a recommendation."
    >
      {failed.length ? (
        <p className="text-sm text-muted-foreground">
          {summary.retrieved_count} of {summary.requested_count} competitors analyzed successfully.{" "}
          {failed.map((item) => item.asin).join(", ")} could not be retrieved.
        </p>
      ) : null}

      <Panel className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="data-table min-w-[640px]">
            <thead>
              <tr>
                <th>Metric</th>
                <th>Your product</th>
                {competitorAsins.map((asin, index) => (
                  <th key={asin}>
                    <CompetitorLabel asin={asin} index={index} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {comparison.comparison.metrics.map((metric) => (
                <MetricRow
                  key={metric.key}
                  metric={metric}
                  competitorAsins={competitorAsins}
                  highlight={highlighted.has(metric.key) || highlighted.has(metric.label)}
                />
              ))}
            </tbody>
          </table>
        </div>
        {comparison.comparison.metrics.some((metric) => metric.note) ? (
          <div className="space-y-1 border-t border-border px-4 py-3">
            {comparison.comparison.metrics
              .filter((metric) => metric.note)
              .map((metric) => (
                <p key={`${metric.key}-note`} className="text-xs text-muted-foreground">
                  {metric.label}: {metric.note}
                </p>
              ))}
          </div>
        ) : null}
      </Panel>

      {comparison.comparison.price_deltas.length ? (
        <Panel className="p-5">
          <h3 className="mb-3 text-[0.95rem] font-semibold">Price differences</h3>
          <div className="space-y-1.5 text-sm leading-6">
            {comparison.comparison.price_deltas.map((delta) => (
              <p key={delta.competitor_asin}>
                {delta.competitor_asin}: {delta.absolute_difference >= 0 ? "+" : ""}
                {delta.absolute_difference.toFixed(2)} {delta.currency} (
                {delta.percentage_difference >= 0 ? "+" : ""}
                {delta.percentage_difference.toFixed(1)}%) versus the target observed price.
              </p>
            ))}
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            Direction is competitor minus target. This is not a recommendation to change price.
          </p>
        </Panel>
      ) : null}

      <Panel className="p-5">
        <h3 className="mb-4 text-[0.95rem] font-semibold">Competitive gaps</h3>
        {comparison.comparison.gaps.length ? (
          <ul className="divide-y divide-border">
            {comparison.comparison.gaps.map((gap) => (
              <li
                key={`${gap.dimension}-${gap.competitor_asin}-${gap.evidence}`}
                className="flex gap-3 py-3 first:pt-0 last:pb-0"
              >
                <SeverityDot severity={gap.severity} />
                <div className="min-w-0 space-y-1">
                  <SeverityLabel severity={gap.severity} />
                  <p className="text-sm leading-6">{gap.evidence}</p>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No measurable listing gaps were generated.</p>
        )}
      </Panel>
    </Section>
  );
}
