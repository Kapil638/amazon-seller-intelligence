"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, Loader2 } from "lucide-react";

import { AICompetitiveIntelligenceView } from "@/components/ai-competitive-intelligence";
import { AIListingIntelligenceView } from "@/components/ai-listing-intelligence";
import { AIListingIntelligenceV2View } from "@/components/ai-listing-intelligence-v2";
import { ImageMediaIntelligenceView } from "@/components/image-media-intelligence";
import { CompetitorComparisonView } from "@/components/competitor-comparison";
import { CompetitorDiscovery } from "@/components/competitor-discovery";
import { CompetitorInput } from "@/components/competitor-input";
import { ListingIntelligence } from "@/components/listing-intelligence";
import { ListingIntelligenceV2 } from "@/components/listing-intelligence-v2";
import { ScoringProfileControls } from "@/components/scoring-profile-controls";
import { ManualProductForm } from "@/components/manual-product-form";
import { ProductResult } from "@/components/product-result";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState, Panel } from "@/components/ui/layout";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  analyzeCompetitors,
  analyzeListing,
  analyzeListingV2,
  discoverCompetitors,
  fetchProduct,
  generateAICompetitiveIntelligence,
  generateAIListingIntelligence,
  generateAIListingIntelligenceV2,
  generateImageIntelligence,
  generateCompetitorSearchQuery,
  listScoringProfiles,
  ProductLookupError,
  reweightListingV2,
} from "@/lib/api";
import { isValidAsin, normalizeAsin } from "@/lib/asin";
import type {
  AICompetitiveIntelligenceResponse,
  AIImageIntelligenceResponse,
  AIListingIntelligenceResponse,
  AIListingIntelligenceV2Response,
  CompetitorComparisonResponse,
  CompetitorDiscoveryResult,
  ListingAnalysisResponse,
  ListingAnalysisV2Response,
  ProductResponse,
} from "@/lib/types";
import { STANDARD_SCORING_PROFILE_ID } from "@/lib/types";

export function ProductLookup() {
  const [liveAsin, setLiveAsin] = useState("");
  const [showManual, setShowManual] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ProductResponse | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<ListingAnalysisResponse | null>(null);
  const [analysisV2, setAnalysisV2] = useState<ListingAnalysisV2Response | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiResult, setAiResult] = useState<AIListingIntelligenceV2Response | null>(null);
  const [legacyAiLoading, setLegacyAiLoading] = useState(false);
  const [legacyAiError, setLegacyAiError] = useState<string | null>(null);
  const [legacyAiResult, setLegacyAiResult] = useState<AIListingIntelligenceResponse | null>(null);
  const [imageAiLoading, setImageAiLoading] = useState(false);
  const [imageAiError, setImageAiError] = useState<string | null>(null);
  const [imageAiResult, setImageAiResult] = useState<AIImageIntelligenceResponse | null>(null);
  const [competitorLoading, setCompetitorLoading] = useState(false);
  const [competitorError, setCompetitorError] = useState<string | null>(null);
  const [comparison, setComparison] = useState<CompetitorComparisonResponse | null>(null);
  const [competitiveAiLoading, setCompetitiveAiLoading] = useState(false);
  const [competitiveAiError, setCompetitiveAiError] = useState<string | null>(null);
  const [competitiveAi, setCompetitiveAi] = useState<AICompetitiveIntelligenceResponse | null>(null);
  const [discoveryOpen, setDiscoveryOpen] = useState(false);
  const [discoveryQuery, setDiscoveryQuery] = useState("");
  const [discoveryQueryLoading, setDiscoveryQueryLoading] = useState(false);
  const [discoveryLoading, setDiscoveryLoading] = useState(false);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<CompetitorDiscoveryResult | null>(null);
  const [selectedCandidateAsins, setSelectedCandidateAsins] = useState<string[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState(STANDARD_SCORING_PROFILE_ID);

  useEffect(() => {
    void listScoringProfiles()
      .then((data) => {
        const orgDefault = data.items.find((item) => item.is_default && !item.is_system && !item.is_archived);
        if (orgDefault) {
          setSelectedProfileId(orgDefault.id);
        }
      })
      .catch(() => {
        /* selector still works with Standard V2 */
      });
  }, []);

  function resetDownstream() {
    setAnalysisError(null);
    setAnalysis(null);
    setAnalysisV2(null);
    setAiError(null);
    setAiResult(null);
    setLegacyAiError(null);
    setLegacyAiResult(null);
    setImageAiError(null);
    setImageAiResult(null);
    setCompetitorError(null);
    setComparison(null);
    setCompetitiveAiError(null);
    setCompetitiveAi(null);
    setDiscoveryOpen(false);
    setDiscoveryQuery("");
    setDiscoveryError(null);
    setDiscovery(null);
    setSelectedCandidateAsins([]);
  }

  async function analyze(nextAsin: string) {
    const normalized = normalizeAsin(nextAsin);

    if (!isValidAsin(normalized)) {
      setResult(null);
      setAnalysis(null);
      setError("Enter a valid 10-character ASIN using letters and numbers only.");
      return;
    }

    setLoading(true);
    setError(null);
    resetDownstream();

    try {
      const next = await fetchProduct(normalized, "amazon.in");
      setResult(next);
    } catch (err) {
      setResult(null);
      if (err instanceof ProductLookupError) {
        setError(err.message);
      } else {
        setError("The listing could not be retrieved right now.");
      }
    } finally {
      setLoading(false);
    }
  }

  function onLiveSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void analyze(liveAsin);
  }

  const idle = !result;

  return (
    <div className={idle ? "flex min-h-[calc(100dvh-7.5rem)] flex-col items-center justify-center" : "space-y-10"}>
      <div className={idle ? "w-full max-w-2xl space-y-6 text-center" : "space-y-5"}>
      <header className="space-y-5">
        <div className="space-y-2">
          <h1 className="text-[2rem] font-semibold leading-tight tracking-tight">
            Amazon Product Intelligence
          </h1>
          <p className="text-[0.9375rem] leading-relaxed text-muted-foreground">
            Research a product, evaluate its listing quality, and benchmark it against competing
            Amazon listings.
          </p>
        </div>

        <form onSubmit={onLiveSubmit} className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <span className="inline-flex h-11 shrink-0 items-center justify-center rounded-md border border-border bg-surface px-3 text-sm text-muted-foreground">
            Amazon.in
          </span>
          <Label htmlFor="live-asin" className="sr-only">
            ASIN
          </Label>
          <Input
            id="live-asin"
            name="asin"
            value={liveAsin}
            onChange={(event) => setLiveAsin(event.target.value.toUpperCase())}
            placeholder="Enter ASIN"
            autoComplete="off"
            spellCheck={false}
            className="h-11 bg-surface font-mono text-[0.9375rem] tracking-wide"
          />
          <Button type="submit" size="lg" disabled={loading} className="h-11 shrink-0">
            {loading ? (
              <>
                <Loader2 className="animate-spin" />
                Retrieving Amazon listing…
              </>
            ) : (
              <>
                Analyze
                <ArrowRight />
              </>
            )}
          </Button>
        </form>
        <div className={`flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground ${idle ? "justify-center" : ""}`}>
          <span>
            Example: <span className="font-mono">B0XXXXXXXX</span>
          </span>
          <button
            type="button"
            className="text-xs text-muted-foreground underline-offset-4 transition-colors duration-200 hover:text-foreground hover:underline"
            onClick={() => setShowManual((value) => !value)}
          >
            {showManual ? "Hide manual entry" : "Enter product manually"}
          </button>
        </div>
      </header>

      {showManual ? (
        <div className="rounded-lg border border-border bg-surface p-5 text-left shadow-[var(--shadow-sm)]">
          <p className="mb-4 text-sm text-muted-foreground">
            Use this when automatic retrieval is unavailable. Details stay in this session and are
            not saved.
          </p>
          <ManualProductForm
            loading={loading}
            onLoadingChange={setLoading}
            onSuccess={(next) => {
              setResult(next);
              resetDownstream();
              setError(null);
              setShowManual(false);
            }}
            onError={(message) => {
              setError(message);
              if (message) {
                setResult(null);
                resetDownstream();
              }
            }}
          />
        </div>
      ) : null}

      {error ? (
        <Alert variant="destructive" className="text-left">
          <AlertTitle>Product could not be retrieved</AlertTitle>
          <AlertDescription className="space-y-3">
            <p>{error}</p>
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="outline" onClick={() => void analyze(liveAsin)}>
                Try again
              </Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setShowManual(true)}>
                Enter product manually
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      ) : null}

      {!result && !error && !loading && !showManual ? (
        <EmptyState
          title="No product analyzed yet"
          description="Enter an Amazon.in ASIN above to begin product intelligence."
        />
      ) : null}
      </div>

      {result ? (
        <>
          <ProductResult product={result.product} source={result.meta.source} />
          <Panel className="p-4">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-medium">Scoring Profile</p>
                <p className="text-xs leading-5 text-muted-foreground">
                  Choose Standard V2 or a custom weight profile before analyzing. Custom scores do
                  not replace Standard V2.
                </p>
              </div>
              <ScoringProfileControls
                selectedId={selectedProfileId}
                onSelect={async (profileId) => {
                  setSelectedProfileId(profileId);
                  if (!analysisV2) {
                    return;
                  }
                  try {
                    const next = await reweightListingV2({
                      scoring_profile_id: profileId,
                      report_id: analysisV2.meta.report_id,
                      analysis: analysisV2.analysis,
                      persist: Boolean(analysisV2.meta.report_id),
                    });
                    setAnalysisV2({
                      ...analysisV2,
                      analysis: next.analysis,
                      custom_score: next.custom_score,
                      meta: {
                        ...analysisV2.meta,
                        scoring_profile: next.custom_score?.profile ?? null,
                      },
                    });
                  } catch (err) {
                    if (err instanceof ProductLookupError) {
                      setAnalysisError(err.message);
                    }
                  }
                }}
              />
            </div>
          </Panel>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              disabled={analysisLoading}
              onClick={async () => {
                setAnalysisLoading(true);
                setAnalysisError(null);
                try {
                  const [legacy, next] = await Promise.all([
                    analyzeListing(result.product, result.meta.source),
                    analyzeListingV2(result.product, result.meta.source, selectedProfileId),
                  ]);
                  setAnalysis(legacy);
                  setAnalysisV2(next);
                  setAiResult(null);
                  setAiError(null);
                  setLegacyAiResult(null);
                  setLegacyAiError(null);
                  setImageAiResult(null);
                  setImageAiError(null);
                } catch (err) {
                  setAnalysis(null);
                  setAnalysisV2(null);
                  setAiResult(null);
                  setAiError(null);
                  setLegacyAiResult(null);
                  setLegacyAiError(null);
                  setImageAiResult(null);
                  setImageAiError(null);
                  if (err instanceof ProductLookupError) {
                    setAnalysisError(err.message);
                  } else {
                    setAnalysisError("Listing quality could not be analyzed right now.");
                  }
                } finally {
                  setAnalysisLoading(false);
                }
              }}
            >
              {analysisLoading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Analyzing listing quality…
                </>
              ) : (
                "Analyze listing quality"
              )}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={discoveryQueryLoading}
              onClick={async () => {
                setDiscoveryQueryLoading(true);
                setDiscoveryError(null);
                try {
                  const next = await generateCompetitorSearchQuery(result.product);
                  setDiscoveryQuery(next.search_query);
                  setDiscoveryOpen(true);
                  setDiscovery(null);
                  setSelectedCandidateAsins([]);
                } catch (err) {
                  if (err instanceof ProductLookupError) {
                    setDiscoveryError(err.message);
                  } else {
                    setDiscoveryError("A competitor search query could not be generated.");
                  }
                } finally {
                  setDiscoveryQueryLoading(false);
                }
              }}
            >
              {discoveryQueryLoading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Preparing search…
                </>
              ) : (
                "Discover competitors"
              )}
            </Button>
          </div>
        </>
      ) : null}

      {analysisError ? (
        <Alert variant="destructive">
          <AlertTitle>Listing analysis unavailable</AlertTitle>
          <AlertDescription>{analysisError}</AlertDescription>
        </Alert>
      ) : null}

      {analysisV2?.meta.persistence_warning ? (
        <Alert>
          <AlertTitle>Analysis succeeded but could not be saved</AlertTitle>
          <AlertDescription>{analysisV2.meta.persistence_warning}</AlertDescription>
        </Alert>
      ) : null}

      {analysisV2?.meta.persisted ? (
        <p className="text-sm text-muted-foreground">
          Saved to History. Reopening that report will not call Rainforest or OpenAI.
        </p>
      ) : null}

      {aiResult?.meta.persistence_warning || imageAiResult?.meta.persistence_warning ? (
        <Alert>
          <AlertTitle>Optional analysis could not be saved</AlertTitle>
          <AlertDescription>
            {aiResult?.meta.persistence_warning || imageAiResult?.meta.persistence_warning}
          </AlertDescription>
        </Alert>
      ) : null}

      {analysisV2 ? (
        <ListingIntelligenceV2
          analysis={analysisV2.analysis}
          customScore={analysisV2.custom_score}
          selector={
            <ScoringProfileControls
              selectedId={selectedProfileId}
              onSelect={async (profileId) => {
                setSelectedProfileId(profileId);
                try {
                  const next = await reweightListingV2({
                    scoring_profile_id: profileId,
                    report_id: analysisV2.meta.report_id,
                    analysis: analysisV2.analysis,
                    persist: Boolean(analysisV2.meta.report_id),
                  });
                  setAnalysisV2({
                    ...analysisV2,
                    analysis: next.analysis,
                    custom_score: next.custom_score,
                    meta: {
                      ...analysisV2.meta,
                      scoring_profile: next.custom_score?.profile ?? null,
                    },
                  });
                } catch (err) {
                  if (err instanceof ProductLookupError) {
                    setAnalysisError(err.message);
                  }
                }
              }}
            />
          }
        />
      ) : null}

      {analysisV2 ? (
        <div>
          <Button
            type="button"
            disabled={aiLoading}
            onClick={async () => {
              setAiLoading(true);
              setAiError(null);
              try {
                const next = await generateAIListingIntelligenceV2(
                  analysisV2.product,
                  analysisV2.analysis,
                  analysisV2.meta.source ?? undefined,
                  analysisV2.meta.report_id,
                );
                setAiResult(next);
              } catch (err) {
                setAiResult(null);
                if (err instanceof ProductLookupError) {
                  setAiError(err.message);
                } else {
                  setAiError("AI strategy could not be generated right now.");
                }
              } finally {
                setAiLoading(false);
              }
            }}
          >
            {aiLoading ? (
              <>
                <Loader2 className="animate-spin" />
                Generating AI strategy…
              </>
            ) : (
              "Generate AI strategy"
            )}
          </Button>
        </div>
      ) : null}

      {aiError ? (
        <Alert variant="destructive">
          <AlertTitle>AI strategy unavailable</AlertTitle>
          <AlertDescription>{aiError}</AlertDescription>
        </Alert>
      ) : null}

      {aiResult ? <AIListingIntelligenceV2View intelligence={aiResult.ai_intelligence} /> : null}

      {analysisV2 ? (
        <div className="space-y-3 rounded-lg border border-border bg-card p-5">
          <div>
            <h2 className="text-[0.95rem] font-semibold">Image & media intelligence</h2>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">
              Optional AI analysis of your listing images and A+ media. Uses OpenAI multimodal
              analysis. Cached results are reused. This does not change listing-quality scores.
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            disabled={imageAiLoading}
            onClick={async () => {
              setImageAiLoading(true);
              setImageAiError(null);
              try {
                const next = await generateImageIntelligence(
                  analysisV2.product,
                  analysisV2.analysis,
                  analysisV2.meta.source ?? undefined,
                  analysisV2.meta.report_id ?? aiResult?.meta.report_id,
                );
                setImageAiResult(next);
              } catch (err) {
                setImageAiResult(null);
                if (err instanceof ProductLookupError) {
                  setImageAiError(err.message);
                } else {
                  setImageAiError("Image analysis could not be generated right now.");
                }
              } finally {
                setImageAiLoading(false);
              }
            }}
          >
            {imageAiLoading ? (
              <>
                <Loader2 className="animate-spin" />
                Analyzing images & media…
              </>
            ) : (
              "Analyze Images & Media"
            )}
          </Button>
        </div>
      ) : null}

      {imageAiError ? (
        <Alert variant="destructive">
          <AlertTitle>Image analysis unavailable</AlertTitle>
          <AlertDescription>{imageAiError}</AlertDescription>
        </Alert>
      ) : null}

      {imageAiResult ? (
        <ImageMediaIntelligenceView
          intelligence={imageAiResult.image_intelligence}
          meta={imageAiResult.meta}
        />
      ) : null}

      {analysis ? (
        <details className="rounded-lg border border-border bg-card">
          <summary className="cursor-pointer px-5 py-3 text-sm font-medium">
            Legacy listing-score-v1 and V1 AI
          </summary>
          <div className="space-y-4 border-t border-border px-1 pb-4">
            <ListingIntelligence analysis={analysis.analysis} />
            <div className="px-4">
              <Button
                type="button"
                variant="outline"
                disabled={legacyAiLoading}
                onClick={async () => {
                  setLegacyAiLoading(true);
                  setLegacyAiError(null);
                  try {
                    const next = await generateAIListingIntelligence(
                      analysis.product,
                      analysis.analysis,
                      analysis.meta.source ?? undefined,
                    );
                    setLegacyAiResult(next);
                  } catch (err) {
                    setLegacyAiResult(null);
                    if (err instanceof ProductLookupError) {
                      setLegacyAiError(err.message);
                    } else {
                      setLegacyAiError("Legacy AI recommendations could not be generated right now.");
                    }
                  } finally {
                    setLegacyAiLoading(false);
                  }
                }}
              >
                {legacyAiLoading ? (
                  <>
                    <Loader2 className="animate-spin" />
                    Generating legacy AI…
                  </>
                ) : (
                  "Generate legacy AI recommendations (v1)"
                )}
              </Button>
            </div>
            {legacyAiError ? (
              <Alert variant="destructive" className="mx-4">
                <AlertTitle>Legacy AI unavailable</AlertTitle>
                <AlertDescription>{legacyAiError}</AlertDescription>
              </Alert>
            ) : null}
            {legacyAiResult ? (
              <AIListingIntelligenceView intelligence={legacyAiResult.ai_intelligence} />
            ) : null}
          </div>
        </details>
      ) : null}

      {discoveryError ? (
        <Alert variant="destructive">
          <AlertTitle>Competitor discovery unavailable</AlertTitle>
          <AlertDescription>{discoveryError}</AlertDescription>
        </Alert>
      ) : null}

      {result && discoveryOpen ? (
        <CompetitorDiscovery
          query={discoveryQuery}
          onQueryChange={setDiscoveryQuery}
          loading={discoveryLoading}
          result={discovery}
          selectedAsins={selectedCandidateAsins}
          onSearch={async (query) => {
            setDiscoveryLoading(true);
            setDiscoveryError(null);
            try {
              const next = await discoverCompetitors(result.product, query);
              setDiscovery(next);
              setSelectedCandidateAsins([]);
            } catch (err) {
              setDiscovery(null);
              if (err instanceof ProductLookupError) {
                setDiscoveryError(err.message);
              } else {
                setDiscoveryError("Amazon search could not be completed right now.");
              }
            } finally {
              setDiscoveryLoading(false);
            }
          }}
          onToggleAsin={(asin) => {
            setSelectedCandidateAsins((current) => {
              if (current.includes(asin)) {
                return current.filter((item) => item !== asin);
              }
              if (current.length >= 3) {
                return current;
              }
              return [...current, asin];
            });
          }}
          onCompare={async () => {
            setCompetitorLoading(true);
            setCompetitorError(null);
            try {
              const next = await analyzeCompetitors(
                result.product,
                selectedCandidateAsins,
                result.meta.source,
              );
              setComparison(next);
              setCompetitiveAi(null);
              setCompetitiveAiError(null);
            } catch (err) {
              setComparison(null);
              setCompetitiveAi(null);
              if (err instanceof ProductLookupError) {
                setCompetitorError(err.message);
              } else {
                setCompetitorError("Selected competitors could not be compared.");
              }
            } finally {
              setCompetitorLoading(false);
            }
          }}
          compareLoading={competitorLoading}
        />
      ) : null}

      {result ? (
        <CompetitorInput
          targetAsin={result.product.asin}
          loading={competitorLoading}
          onAnalyze={async (asins) => {
            setCompetitorLoading(true);
            setCompetitorError(null);
            try {
              const next = await analyzeCompetitors(result.product, asins, result.meta.source);
              setComparison(next);
              setCompetitiveAi(null);
              setCompetitiveAiError(null);
            } catch (err) {
              setComparison(null);
              setCompetitiveAi(null);
              if (err instanceof ProductLookupError) {
                setCompetitorError(err.message);
              } else {
                setCompetitorError("Selected competitors could not be compared.");
              }
            } finally {
              setCompetitorLoading(false);
            }
          }}
        />
      ) : null}

      {competitorError ? (
        <Alert variant="destructive">
          <AlertTitle>Competitor comparison unavailable</AlertTitle>
          <AlertDescription>{competitorError}</AlertDescription>
        </Alert>
      ) : null}

      {comparison ? (
        <>
          <CompetitorComparisonView comparison={comparison} />
          <div>
            <Button
              type="button"
              disabled={competitiveAiLoading}
              onClick={async () => {
                setCompetitiveAiLoading(true);
                setCompetitiveAiError(null);
                try {
                  const next = await generateAICompetitiveIntelligence(comparison);
                  setCompetitiveAi(next);
                } catch (err) {
                  setCompetitiveAi(null);
                  if (err instanceof ProductLookupError) {
                    setCompetitiveAiError(err.message);
                  } else {
                    setCompetitiveAiError("Competitive insights could not be generated.");
                  }
                } finally {
                  setCompetitiveAiLoading(false);
                }
              }}
            >
              {competitiveAiLoading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Analyzing competitive position…
                </>
              ) : (
                "Generate AI competitive insights"
              )}
            </Button>
          </div>
        </>
      ) : null}

      {competitiveAiError ? (
        <Alert variant="destructive">
          <AlertTitle>Competitive insights unavailable</AlertTitle>
          <AlertDescription>{competitiveAiError}</AlertDescription>
        </Alert>
      ) : null}

      {competitiveAi ? (
        <AICompetitiveIntelligenceView intelligence={competitiveAi.ai_intelligence} />
      ) : null}
    </div>
  );
}
