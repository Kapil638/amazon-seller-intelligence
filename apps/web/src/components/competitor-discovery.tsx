"use client";

import { useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { CompetitorDiscoveryResult, DiscoveredProductCandidate } from "@/lib/types";

const MAX_SELECTED = 3;

function formatValue(value: string | number | null | undefined, suffix = ""): string {
  if (value == null || value === "") {
    return "Not available";
  }
  return `${value}${suffix}`;
}

function formatPrice(amount: number | null, currency: string | null): string {
  if (amount == null) {
    return "Not available";
  }
  if (currency === "INR") {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 0,
    }).format(amount);
  }
  return `${currency ?? ""} ${amount}`.trim();
}

function CandidateCard({
  candidate,
  selected,
  disabled,
  onToggle,
}: {
  candidate: DiscoveredProductCandidate;
  selected: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  return (
    <label className="flex cursor-pointer gap-3 rounded-lg border border-border p-3">
      <input
        type="checkbox"
        className="mt-1"
        checked={selected}
        disabled={disabled && !selected}
        onChange={onToggle}
      />
      {candidate.image ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={candidate.image}
          alt=""
          className="h-16 w-16 shrink-0 rounded-md object-cover bg-muted"
        />
      ) : (
        <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-md bg-muted text-xs text-muted-foreground">
          No image
        </div>
      )}
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm font-medium leading-5">{candidate.title}</p>
        <p className="font-mono text-xs text-muted-foreground">ASIN: {candidate.asin}</p>
        <p className="text-xs text-muted-foreground">
          {formatPrice(candidate.price, candidate.currency)} ·{" "}
          {formatValue(candidate.rating, " ★")} · {formatValue(candidate.review_count)} reviews
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary">Relevance: {candidate.relevance_score}</Badge>
          <span className="text-xs text-muted-foreground">
            Search position: {candidate.position ?? "Not available"}
          </span>
          {candidate.is_sponsored ? <Badge variant="outline">Sponsored</Badge> : null}
        </div>
      </div>
    </label>
  );
}

export function CompetitorDiscovery({
  query,
  onQueryChange,
  loading,
  result,
  selectedAsins,
  onSearch,
  onToggleAsin,
  onCompare,
  compareLoading,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  loading: boolean;
  result: CompetitorDiscoveryResult | null;
  selectedAsins: string[];
  onSearch: (query: string) => void;
  onToggleAsin: (asin: string) => void;
  onCompare: () => void;
  compareLoading: boolean;
}) {
  const [localError, setLocalError] = useState<string | null>(null);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (trimmed.length < 2) {
      setLocalError("Enter a search query with at least two characters.");
      return;
    }
    if (trimmed.length > 80) {
      setLocalError("Search query must be 80 characters or fewer.");
      return;
    }
    setLocalError(null);
    onSearch(trimmed);
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
            Competitor Discovery
          </p>
          <CardTitle className="text-2xl">Suggested competitors</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="amazon-search-query">Amazon search query</Label>
              <Input
                id="amazon-search-query"
                value={query}
                onChange={(event) => onQueryChange(event.target.value)}
                disabled={loading}
              />
              <p className="text-xs text-muted-foreground">
                This is a suggested Amazon search, not a list of confirmed competitors.
                You can edit it before searching.
              </p>
            </div>
            {localError ? <p className="text-sm text-destructive">{localError}</p> : null}
            <Button type="submit" size="lg" disabled={loading} className="w-full sm:w-auto">
              {loading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Searching Amazon...
                </>
              ) : (
                "Search Amazon"
              )}
            </Button>
          </form>
        </CardContent>
      </Card>

      {result ? (
        <Card>
          <CardHeader className="space-y-2">
            <CardTitle>Candidate listings</CardTitle>
            <p className="text-sm text-muted-foreground">
              Search used: {result.search_query}. Relevance is title/category similarity to your
              listing, not sales or market position. Select up to {MAX_SELECTED}.
            </p>
            <p className="text-sm font-medium">
              {selectedAsins.length} / {MAX_SELECTED} selected
            </p>
          </CardHeader>
          <CardContent className="space-y-3">
            {result.candidates.length ? (
              result.candidates.map((candidate) => (
                <CandidateCard
                  key={candidate.asin}
                  candidate={candidate}
                  selected={selectedAsins.includes(candidate.asin)}
                  disabled={
                    selectedAsins.length >= MAX_SELECTED && !selectedAsins.includes(candidate.asin)
                  }
                  onToggle={() => onToggleAsin(candidate.asin)}
                />
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                No relevant Amazon results were found for this search.
              </p>
            )}
            <Button
              type="button"
              size="lg"
              disabled={compareLoading || selectedAsins.length < 1}
              onClick={onCompare}
            >
              {compareLoading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Fetching competitor data...
                </>
              ) : (
                "Compare Selected"
              )}
            </Button>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
