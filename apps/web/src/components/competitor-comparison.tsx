"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type {
  ComparisonMetric,
  CompetitorComparisonResponse,
  GapSeverity,
} from "@/lib/types";

const SEVERITY_STYLES: Record<GapSeverity, string> = {
  high: "border-transparent bg-destructive text-destructive-foreground",
  medium: "border-transparent bg-amber-100 text-amber-900",
  low: "border-transparent bg-secondary text-secondary-foreground",
};

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

function CompetitorLabel({ asin, index }: { asin: string; index: number }) {
  const letter = String.fromCharCode(65 + index);
  return (
    <div>
      <p>Competitor {letter}</p>
      <p className="font-mono text-xs font-normal text-muted-foreground">{asin}</p>
    </div>
  );
}

function MetricRow({
  metric,
  competitorAsins,
}: {
  metric: ComparisonMetric;
  competitorAsins: string[];
}) {
  return (
    <tr className="border-t border-border">
      <th className="px-3 py-2 text-left text-sm font-medium">{metric.label}</th>
      <td className="px-3 py-2 text-sm">{formatValue(metric.target_value, metric.key)}</td>
      {competitorAsins.map((asin) => (
        <td key={`${metric.key}-${asin}`} className="px-3 py-2 text-sm">
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

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
            Competitor Comparison
          </p>
          <CardTitle className="text-2xl">Observed catalog and listing metrics</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {failed.length ? (
            <p className="text-sm text-muted-foreground">
              {summary.retrieved_count} of {summary.requested_count} competitors analyzed
              successfully. {failed.map((item) => item.asin).join(", ")} could not be retrieved.
            </p>
          ) : null}
          <p className="text-sm text-muted-foreground">
            Values are observed facts. A higher or lower number is not automatically better.
            Lower price is not treated as a recommendation.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] border-collapse">
              <thead>
                <tr className="text-left text-sm text-muted-foreground">
                  <th className="px-3 py-2 font-medium">Metric</th>
                  <th className="px-3 py-2 font-medium">Your Product</th>
                  {competitorAsins.map((asin, index) => (
                    <th key={asin} className="px-3 py-2 font-medium">
                      <CompetitorLabel asin={asin} index={index} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {comparison.comparison.metrics.map((metric) => (
                  <MetricRow key={metric.key} metric={metric} competitorAsins={competitorAsins} />
                ))}
              </tbody>
            </table>
          </div>
          {comparison.comparison.metrics
            .filter((metric) => metric.note)
            .map((metric) => (
              <p key={`${metric.key}-note`} className="text-xs text-muted-foreground">
                {metric.label}: {metric.note}
              </p>
            ))}
        </CardContent>
      </Card>

      {comparison.comparison.price_deltas.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Price differences</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {comparison.comparison.price_deltas.map((delta) => (
              <p key={delta.competitor_asin} className="text-sm leading-6">
                {delta.competitor_asin}: {delta.absolute_difference >= 0 ? "+" : ""}
                {delta.absolute_difference.toFixed(2)} {delta.currency} (
                {delta.percentage_difference >= 0 ? "+" : ""}
                {delta.percentage_difference.toFixed(1)}%) versus the target observed price.
              </p>
            ))}
            <p className="text-xs text-muted-foreground">
              Direction is competitor minus target. This is not a recommendation to change price.
            </p>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Competitive gaps</CardTitle>
        </CardHeader>
        <CardContent>
          {comparison.comparison.gaps.length ? (
            <ul className="space-y-3">
              {comparison.comparison.gaps.map((gap) => (
                <li
                  key={`${gap.dimension}-${gap.competitor_asin}-${gap.evidence}`}
                  className="flex gap-3"
                >
                  <Badge className={cn("mt-0.5 uppercase", SEVERITY_STYLES[gap.severity])}>
                    {gap.severity}
                  </Badge>
                  <p className="text-sm leading-6 text-foreground">{gap.evidence}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No measurable listing gaps were generated.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
