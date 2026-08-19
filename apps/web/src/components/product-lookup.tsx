"use client";

import { useState, type FormEvent } from "react";
import { ArrowRight, Loader2 } from "lucide-react";

import { AICompetitiveIntelligenceView } from "@/components/ai-competitive-intelligence";
import { AIListingIntelligenceView } from "@/components/ai-listing-intelligence";
import { CompetitorComparisonView } from "@/components/competitor-comparison";
import { CompetitorDiscovery } from "@/components/competitor-discovery";
import { CompetitorInput } from "@/components/competitor-input";
import { ListingIntelligence } from "@/components/listing-intelligence";
import { ManualProductForm } from "@/components/manual-product-form";
import { ProductResult } from "@/components/product-result";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/layout";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  analyzeCompetitors,
  analyzeListing,
  discoverCompetitors,
  fetchProduct,
  generateAICompetitiveIntelligence,
  generateAIListingIntelligence,
  generateCompetitorSearchQuery,
  ProductLookupError,
} from "@/lib/api";
import { isValidAsin, normalizeAsin } from "@/lib/asin";
import type {
  AICompetitiveIntelligenceResponse,
  AIListingIntelligenceResponse,
  CompetitorComparisonResponse,
  CompetitorDiscoveryResult,
  ListingAnalysisResponse,
  ProductResponse,
} from "@/lib/types";

export function ProductLookup() {
  const [liveAsin, setLiveAsin] = useState("");
  const [showManual, setShowManual] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ProductResponse | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<ListingAnalysisResponse | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiResult, setAiResult] = useState<AIListingIntelligenceResponse | null>(null);
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

  function resetDownstream() {
    setAnalysisError(null);
    setAnalysis(null);
    setAiError(null);
    setAiResult(null);
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
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              disabled={analysisLoading}
              onClick={async () => {
                setAnalysisLoading(true);
                setAnalysisError(null);
                try {
                  const next = await analyzeListing(result.product, result.meta.source);
                  setAnalysis(next);
                  setAiResult(null);
                  setAiError(null);
                } catch (err) {
                  setAnalysis(null);
                  setAiResult(null);
                  setAiError(null);
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

      {analysis ? (
        <>
          <ListingIntelligence analysis={analysis.analysis} />
          <div>
            <Button
              type="button"
              disabled={aiLoading}
              onClick={async () => {
                setAiLoading(true);
                setAiError(null);
                try {
                  const next = await generateAIListingIntelligence(
                    analysis.product,
                    analysis.analysis,
                    analysis.meta.source ?? undefined,
                  );
                  setAiResult(next);
                } catch (err) {
                  setAiResult(null);
                  if (err instanceof ProductLookupError) {
                    setAiError(err.message);
                  } else {
                    setAiError("AI recommendations could not be generated right now.");
                  }
                } finally {
                  setAiLoading(false);
                }
              }}
            >
              {aiLoading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Generating AI recommendations…
                </>
              ) : (
                "Generate AI recommendations"
              )}
            </Button>
          </div>
        </>
      ) : null}

      {aiError ? (
        <Alert variant="destructive">
          <AlertTitle>AI recommendations unavailable</AlertTitle>
          <AlertDescription>{aiError}</AlertDescription>
        </Alert>
      ) : null}

      {aiResult ? <AIListingIntelligenceView intelligence={aiResult.ai_intelligence} /> : null}

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
