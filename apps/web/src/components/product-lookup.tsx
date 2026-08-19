"use client";

import { useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";

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
import { Card, CardContent } from "@/components/ui/card";
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
import { cn } from "@/lib/utils";
import type {
  AICompetitiveIntelligenceResponse,
  AIListingIntelligenceResponse,
  CompetitorComparisonResponse,
  CompetitorDiscoveryResult,
  ListingAnalysisResponse,
  ProductResponse,
} from "@/lib/types";

const SAMPLE_ASINS = ["B0TEST0001", "B0TEST0002", "B0TEST0003"] as const;

type Mode = "live" | "demo" | "manual";

export function ProductLookup() {
  const [mode, setMode] = useState<Mode>("live");
  const [liveAsin, setLiveAsin] = useState("");
  const [demoAsin, setDemoAsin] = useState("B0TEST0001");
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

    try {
      const next = await fetchProduct(normalized, "amazon.in");
      setResult(next);
    } catch (err) {
      setResult(null);
      if (err instanceof ProductLookupError) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  }

  function onLiveSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void analyze(liveAsin);
  }

  function onDemoSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void analyze(demoAsin);
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-8">
      <header className="space-y-3">
        <p className="text-xs font-medium uppercase tracking-[0.22em] text-muted-foreground">
          Milestone 8
        </p>
        <h1 className="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
          Amazon Seller Intelligence
        </h1>
        <p className="max-w-xl text-base text-muted-foreground">
          Analyze any Amazon product, then discover and compare competitor listings.
        </p>
      </header>

      <div className="grid grid-cols-3 rounded-lg border border-border bg-card p-1">
        {(
          [
            ["live", "Analyze ASIN"],
            ["demo", "Quick Demo"],
            ["manual", "Manual Product"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={cn(
              "rounded-md px-2 py-2 text-xs font-medium transition-colors sm:text-sm",
              mode === id
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setMode(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {mode === "live" ? (
        <Card>
          <CardContent className="p-6">
            <p className="mb-4 text-sm text-muted-foreground">
              Enter a real Amazon.in ASIN. The backend retrieves listing details
              through the configured catalog provider.
            </p>
            <form onSubmit={onLiveSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="live-asin">ASIN</Label>
                <Input
                  id="live-asin"
                  name="asin"
                  value={liveAsin}
                  onChange={(event) => setLiveAsin(event.target.value.toUpperCase())}
                  placeholder="B0XXXXXXXX"
                  autoComplete="off"
                  spellCheck={false}
                  className="font-mono tracking-wide"
                />
              </div>
              <Button type="submit" size="lg" disabled={loading} className="w-full sm:w-auto">
                {loading ? (
                  <>
                    <Loader2 className="animate-spin" />
                    Analyzing…
                  </>
                ) : (
                  "Analyze Product"
                )}
              </Button>
            </form>
          </CardContent>
        </Card>
      ) : null}

      {mode === "demo" ? (
        <Card>
          <CardContent className="p-6">
            <p className="mb-4 text-sm text-muted-foreground">
              Enter one of the mock ASINs to see sample product data. No Amazon
              connection is used.
            </p>
            <form onSubmit={onDemoSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="asin">ASIN</Label>
                <Input
                  id="asin"
                  name="asin"
                  value={demoAsin}
                  onChange={(event) => setDemoAsin(event.target.value.toUpperCase())}
                  placeholder="B0TEST0001"
                  autoComplete="off"
                  spellCheck={false}
                  className="font-mono tracking-wide"
                />
              </div>
              <Button type="submit" size="lg" disabled={loading} className="w-full sm:w-auto">
                {loading ? (
                  <>
                    <Loader2 className="animate-spin" />
                    Analyzing…
                  </>
                ) : (
                  "Analyze Product"
                )}
              </Button>
            </form>

            <div className="mt-5 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <span>Try</span>
              {SAMPLE_ASINS.map((sample) => (
                <button
                  key={sample}
                  type="button"
                  className="rounded-md border border-border bg-background px-2 py-1 font-mono text-xs text-foreground hover:bg-accent"
                  onClick={() => {
                    setDemoAsin(sample);
                    void analyze(sample);
                  }}
                >
                  {sample}
                </button>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}

      {mode === "manual" ? (
        <Card>
          <CardContent className="p-6">
            <p className="mb-6 text-sm text-muted-foreground">
              Fallback for draft listings or when live lookup is unavailable. Enter
              publicly visible details by hand. Data stays local and is not saved.
            </p>
            <ManualProductForm
              loading={loading}
              onLoadingChange={setLoading}
              onSuccess={(next) => {
                setResult(next);
                setAnalysis(null);
                setAnalysisError(null);
                setAiResult(null);
                setAiError(null);
                setComparison(null);
                setCompetitorError(null);
                setCompetitiveAi(null);
                setCompetitiveAiError(null);
                setDiscoveryOpen(false);
                setDiscoveryQuery("");
                setDiscoveryError(null);
                setDiscovery(null);
                setSelectedCandidateAsins([]);
                setError(null);
              }}
              onError={(message) => {
                setError(message);
                if (message) {
                  setResult(null);
                  setAnalysis(null);
                  setAiResult(null);
                  setAiError(null);
                  setComparison(null);
                  setCompetitorError(null);
                  setCompetitiveAi(null);
                  setCompetitiveAiError(null);
                  setDiscoveryOpen(false);
                  setDiscovery(null);
                  setSelectedCandidateAsins([]);
                }
              }}
            />
          </CardContent>
        </Card>
      ) : null}

      {error ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn’t load product</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      {result ? (
        <>
          <ProductResult product={result.product} source={result.meta.source} />
          <div className="flex flex-wrap gap-3">
            <Button
              type="button"
              size="lg"
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
                    setAnalysisError("Something went wrong while analyzing this listing.");
                  }
                } finally {
                  setAnalysisLoading(false);
                }
              }}
            >
              {analysisLoading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Analyzing listing…
                </>
              ) : (
                "Analyze Listing"
              )}
            </Button>
            <Button
              type="button"
              size="lg"
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
                    setDiscoveryError("Something went wrong while generating a search query.");
                  }
                } finally {
                  setDiscoveryQueryLoading(false);
                }
              }}
            >
              {discoveryQueryLoading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Preparing search...
                </>
              ) : (
                "Discover Competitors"
              )}
            </Button>
          </div>
        </>
      ) : null}

      {analysisError ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn’t analyze listing</AlertTitle>
          <AlertDescription>{analysisError}</AlertDescription>
        </Alert>
      ) : null}

      {analysis ? (
        <>
          <ListingIntelligence analysis={analysis.analysis} />
          <div>
            <Button
              type="button"
              size="lg"
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
                    setAiError("Something went wrong while generating AI recommendations.");
                  }
                } finally {
                  setAiLoading(false);
                }
              }}
            >
              {aiLoading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Analyzing listing strategy...
                </>
              ) : (
                "Generate AI Recommendations"
              )}
            </Button>
          </div>
        </>
      ) : null}

      {aiError ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn’t generate AI recommendations</AlertTitle>
          <AlertDescription>{aiError}</AlertDescription>
        </Alert>
      ) : null}

      {aiResult ? <AIListingIntelligenceView intelligence={aiResult.ai_intelligence} /> : null}

      {discoveryError ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn’t discover competitors</AlertTitle>
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
                setDiscoveryError("Something went wrong while discovering competitors.");
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
                setCompetitorError("Something went wrong while comparing competitors.");
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
                setCompetitorError("Something went wrong while comparing competitors.");
              }
            } finally {
              setCompetitorLoading(false);
            }
          }}
        />
      ) : null}

      {competitorError ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn’t compare competitors</AlertTitle>
          <AlertDescription>{competitorError}</AlertDescription>
        </Alert>
      ) : null}

      {comparison ? (
        <>
          <CompetitorComparisonView comparison={comparison} />
          <div>
            <Button
              type="button"
              size="lg"
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
                    setCompetitiveAiError("Something went wrong while generating competitive insights.");
                  }
                } finally {
                  setCompetitiveAiLoading(false);
                }
              }}
            >
              {competitiveAiLoading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Analyzing competitive position...
                </>
              ) : (
                "Generate AI Competitive Insights"
              )}
            </Button>
          </div>
        </>
      ) : null}

      {competitiveAiError ? (
        <Alert variant="destructive">
          <AlertTitle>Couldn’t generate competitive insights</AlertTitle>
          <AlertDescription>{competitiveAiError}</AlertDescription>
        </Alert>
      ) : null}

      {competitiveAi ? (
        <AICompetitiveIntelligenceView intelligence={competitiveAi.ai_intelligence} />
      ) : null}
    </div>
  );
}
