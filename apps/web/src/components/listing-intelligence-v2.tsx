"use client";

import type { ReactNode } from "react";

import { Panel, Section } from "@/components/ui/layout";
import { ScoreBar, SeverityDot, SeverityLabel } from "@/components/ui/score";
import { cn } from "@/lib/utils";
import type {
  CoverageGroup,
  CustomScoreResult,
  Finding,
  FindingSeverity,
  ListingAnalysisV2,
  MarketSignals,
  V2Recommendation,
} from "@/lib/types";

const QUALITY_SECTIONS = [
  ["title", "Title optimization"],
  ["bullets", "Bullet SEO readiness"],
  ["description_a_plus", "Description & A+"],
  ["media_coverage", "Media coverage"],
  ["content_structure", "Content structure"],
] as const;

const COVERAGE_GROUPS = [
  ["core_listing_content", "Core listing content"],
  ["media", "Media"],
  ["enhanced_content", "Enhanced content"],
  ["category_context", "Category context"],
  ["market_signals", "Market signals"],
] as const;

const SEVERITY_ORDER: FindingSeverity[] = ["high", "medium", "low", "info"];

function FindingsList({ findings }: { findings: Finding[] }) {
  if (!findings.length) {
    return <p className="text-sm text-muted-foreground">No findings were generated.</p>;
  }

  return (
    <div className="space-y-6">
      {SEVERITY_ORDER.map((severity) => {
        const items = findings.filter((finding) => finding.severity === severity);
        if (!items.length) {
          return null;
        }
        const heading =
          severity === "high"
            ? "High priority"
            : severity === "medium"
              ? "Medium"
              : severity === "low"
                ? "Low"
                : "Notes";
        return (
          <div key={severity} className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">{heading}</p>
            <ul className="divide-y divide-border">
              {items.map((finding) => (
                <li key={`${finding.code}-${finding.message}`} className="flex gap-3 py-3 first:pt-0 last:pb-0">
                  <SeverityDot severity={finding.severity} />
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-medium">{finding.category}</p>
                      <SeverityLabel severity={finding.severity} />
                    </div>
                    <p className="text-sm leading-6 text-muted-foreground">{finding.message}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

function RecommendationsList({ recommendations }: { recommendations: V2Recommendation[] }) {
  if (!recommendations.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No deterministic actions were generated for this listing.
      </p>
    );
  }
  return (
    <ol className="space-y-3">
      {recommendations.map((item, index) => (
        <li key={`${item.code}-${index}`} className="flex gap-3 text-sm leading-6">
          <span className="w-5 shrink-0 tabular-nums text-muted-foreground">{index + 1}.</span>
          <span>{item.action}</span>
        </li>
      ))}
    </ol>
  );
}

function MarketSignalsPanel({ signals }: { signals: MarketSignals }) {
  const rows: Array<[string, string]> = [
    ["Rating", signals.rating != null ? String(signals.rating) : "Not in payload"],
    ["Review count", signals.review_count != null ? String(signals.review_count) : "Not in payload"],
    [
      "Price",
      signals.price ? `${signals.price.amount} ${signals.price.currency}` : "Not in payload",
    ],
    ["Availability", signals.availability || signals.availability_type || "Not in payload"],
    [
      "Sold by Amazon",
      signals.is_sold_by_amazon == null ? "Unknown" : signals.is_sold_by_amazon ? "Yes" : "No",
    ],
    ["Seller", signals.seller?.name || "Not in payload"],
    [
      "BSR",
      signals.bsr_ranks.length
        ? signals.bsr_ranks.map((item) => `#${item.rank} in ${item.category}`).join("; ")
        : "Not in payload",
    ],
    ["Recent sales text", signals.recent_sales_text || "Not in payload"],
  ];

  return (
    <dl className="space-y-3">
      {rows.map(([label, value]) => (
        <div key={label} className="grid gap-1 sm:grid-cols-[9rem_1fr]">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="text-sm leading-6">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function CoveragePanel({ group }: { group: CoverageGroup }) {
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium">{group.name.replaceAll("_", " ")}</p>
        <p className="text-xs tabular-nums text-muted-foreground">
          {group.available}/{group.expected} · {group.percentage}%
        </p>
      </div>
      <ul className="space-y-1">
        {group.fields.map((field) => (
          <li key={field.name} className="flex justify-between gap-3 text-xs text-muted-foreground">
            <span>{field.name}</span>
            <span className="tabular-nums">{field.evidence_state.replaceAll("_", " ")}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ListingIntelligenceV2({
  analysis,
  customScore,
  selector,
  historical = false,
}: {
  analysis: ListingAnalysisV2;
  customScore?: CustomScoreResult | null;
  selector?: ReactNode;
  historical?: boolean;
}) {
  return (
    <Section
      title="Listing quality"
      description={`Deterministic listing-score-${analysis.score_version}. This is structural coverage, not a sales or conversion prediction. Bands are internal heuristics, not Amazon performance grades.`}
    >
      {!historical && selector ? (
        <Panel className="p-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">Scoring Profile</p>
              <p className="text-xs leading-5 text-muted-foreground">
                Custom profiles change how section scores are weighted. They do not change the
                underlying analysis.
              </p>
            </div>
            {selector}
          </div>
        </Panel>
      ) : null}

      <Panel className="p-5">
        <div className="grid gap-8 lg:grid-cols-[16rem_1fr] lg:items-start">
          <div className="space-y-5">
            <div>
              <p className="text-xs text-muted-foreground">Standard Listing Quality Score</p>
              <p className="mt-1 text-[2rem] font-semibold tabular-nums tracking-tight">
                {analysis.listing_quality_score}
                <span className="text-base font-medium text-muted-foreground"> / 100</span>
              </p>
              <p className="mt-1 text-xs text-muted-foreground">Standard V2 · universal benchmark</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Custom Listing Quality Score</p>
              {customScore ? (
                <>
                  <p className="mt-1 text-[2rem] font-semibold tabular-nums tracking-tight">
                    {customScore.custom_listing_quality_score}
                    <span className="text-base font-medium text-muted-foreground"> / 100</span>
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Profile: {customScore.profile.profile_name}
                  </p>
                </>
              ) : (
                <>
                  <p className="mt-1 text-[2rem] font-semibold tabular-nums tracking-tight text-muted-foreground">
                    —
                    <span className="text-base font-medium"> / 100</span>
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Select or create a custom profile above to compare against Standard V2.
                  </p>
                </>
              )}
            </div>
            <p className={cn("text-xs capitalize text-muted-foreground")}>{analysis.status}</p>
          </div>
          <div className="space-y-3">
            {QUALITY_SECTIONS.map(([key, label]) => {
              const section = analysis.sections[key];
              return <ScoreBar key={key} label={label} score={section.score} max={section.max_score} />;
            })}
          </div>
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel className="p-5">
          <h3 className="mb-2 text-[0.95rem] font-semibold">Market signals</h3>
          <p className="mb-4 text-xs text-muted-foreground">
            Observed marketplace facts. These are not part of the listing quality score.
          </p>
          <MarketSignalsPanel signals={analysis.market_signals} />
        </Panel>
        <Panel className="p-5">
          <h3 className="mb-2 text-[0.95rem] font-semibold">Data coverage</h3>
          <p className="mb-4 text-xs text-muted-foreground">
            Evidence available for this analysis. Unknown is not the same as absent, and missing
            provider fields are not listing defects.
          </p>
          <p className="mb-4 text-sm tabular-nums">
            Overall evidence {analysis.data_coverage.overall_percentage}%
          </p>
          <div className="space-y-4">
            {COVERAGE_GROUPS.map(([key]) => (
              <CoveragePanel key={key} group={analysis.data_coverage[key]} />
            ))}
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel className="p-5">
          <h3 className="mb-4 text-[0.95rem] font-semibold">Findings</h3>
          <FindingsList findings={analysis.findings} />
        </Panel>
        <Panel className="p-5">
          <h3 className="mb-4 text-[0.95rem] font-semibold">Recommended actions</h3>
          <RecommendationsList recommendations={analysis.recommendations} />
        </Panel>
      </div>
    </Section>
  );
}
