"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";

import { AIListingIntelligenceV2View } from "@/components/ai-listing-intelligence-v2";
import { ImageMediaIntelligenceView } from "@/components/image-media-intelligence";
import { ListingIntelligenceV2 } from "@/components/listing-intelligence-v2";
import { ScoringProfileSnapshotView } from "@/components/scoring-profile-controls";
import { ProductResult } from "@/components/product-result";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel, Section } from "@/components/ui/layout";
import { fetchSavedAnalysis, ProductLookupError } from "@/lib/api";
import type { ProductSource, SavedAnalysisDetail } from "@/lib/types";

function formatDateTime(value: string | null): string {
  if (!value) {
    return "Not recorded";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Not recorded";
  }
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function sourceLabel(source: string | null | undefined): string {
  if (source === "rainforest") return "Rainforest";
  if (source === "mock") return "Mock catalog";
  if (source === "manual") return "Manual";
  if (source === "amazon_public") return "Amazon.in public";
  return source || "Not recorded";
}

export function HistoricalAnalysis({ reportId }: { reportId: string }) {
  const [report, setReport] = useState<SavedAnalysisDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const next = await fetchSavedAnalysis(reportId);
        if (!cancelled) {
          setReport(next);
        }
      } catch (err) {
        if (!cancelled) {
          setReport(null);
          setError(err instanceof ProductLookupError ? err.message : "This saved analysis could not be opened.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [reportId]);

  const productSource = (report?.meta.product_source ?? undefined) as ProductSource | undefined;

  return (
    <div className="space-y-6">
      <PageHeader
        title={report?.display_name || report?.product.title || "Historical analysis"}
        description="This is a historical report. It shows the product snapshot and analysis as they were saved. It is not current Amazon data."
      >
        <Button asChild variant="outline">
          <Link href="/history">Back to History</Link>
        </Button>
      </PageHeader>

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Opening saved analysis…
        </div>
      ) : null}

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Report not found</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {report ? (
        <>
          <Section title="Historical Analysis" eyebrow="Saved report">
            <Panel className="p-5">
              <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <MetaItem label="Analyzed" value={formatDateTime(report.meta.analyzed_at)} />
                <MetaItem label="Product data fetched" value={formatDateTime(report.meta.product_fetched_at)} />
                <MetaItem label="Source" value={sourceLabel(report.meta.product_source)} />
                <MetaItem
                  label="Listing score version"
                  value={report.meta.listing_score_version || "Not recorded"}
                />
                <MetaItem label="AI prompt version" value={report.meta.ai_prompt_version || "Not generated"} />
                <MetaItem
                  label="Image prompt version"
                  value={report.meta.image_prompt_version || "Not generated"}
                />
                {report.meta.ai_model ? (
                  <MetaItem
                    label="AI model"
                    value={`${report.meta.ai_provider || "openai"} / ${report.meta.ai_model}`}
                  />
                ) : null}
                <MetaItem label="Status" value={report.meta.status} />
              </dl>
              <p className="mt-4 text-sm text-muted-foreground">
                Analyzed on {formatDateTime(report.meta.analyzed_at)}. Opening this page does not call
                Rainforest or OpenAI.
              </p>
            </Panel>
          </Section>

          <ProductResult product={report.product} source={productSource} />
          <ListingIntelligenceV2
            analysis={report.analysis}
            customScore={report.custom_score}
            historical
          />
          {report.custom_score ? (
            <ScoringProfileSnapshotView
              profileName={report.custom_score.profile.profile_name}
              weights={report.custom_score.profile.weights}
            />
          ) : null}
          {report.ai_intelligence ? (
            <AIListingIntelligenceV2View intelligence={report.ai_intelligence} />
          ) : (
            <p className="text-sm text-muted-foreground">AI Strategy V2 was not generated for this report.</p>
          )}
          {report.image_intelligence ? (
            <ImageMediaIntelligenceView intelligence={report.image_intelligence} />
          ) : (
            <p className="text-sm text-muted-foreground">
              Image & Media Intelligence was not generated for this report.
            </p>
          )}
        </>
      ) : null}
    </div>
  );
}

function MetaItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-sm font-medium">{value}</dd>
    </div>
  );
}
