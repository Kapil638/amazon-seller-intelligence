"use client";

import { useState, type FormEvent } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Panel, Section } from "@/components/ui/layout";
import type { CompetitorDiscoveryResult, DiscoveredProductCandidate } from "@/lib/types";
import { cn } from "@/lib/utils";

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

function CandidateRow({
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
    <label
      className={cn(
        "flex cursor-pointer gap-4 border-b border-border px-4 py-3 transition-colors duration-200 last:border-b-0 hover:bg-surface-subtle",
        selected && "bg-surface-subtle",
        disabled && !selected && "cursor-not-allowed opacity-50",
      )}
    >
      <input
        type="checkbox"
        className="mt-2"
        checked={selected}
        disabled={disabled && !selected}
        onChange={onToggle}
      />
      {candidate.image ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={candidate.image}
          alt=""
          className="h-14 w-14 shrink-0 rounded-md bg-surface-subtle object-cover"
        />
      ) : (
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-md bg-surface-subtle text-[11px] text-muted-foreground">
          No image
        </div>
      )}
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm font-medium leading-5">{candidate.title}</p>
        <p className="text-xs text-muted-foreground">{candidate.asin}</p>
        <p className="text-xs text-muted-foreground">
          {formatPrice(candidate.price, candidate.currency)}
          {" · "}
          {formatValue(candidate.rating, " ★")}
          {" · "}
          {formatValue(candidate.review_count)} reviews
        </p>
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span>Similarity {candidate.relevance_score}</span>
          {candidate.is_sponsored ? <span>Sponsored</span> : null}
        </div>
      </div>
      <span className="self-center text-xs font-medium text-muted-foreground">
        {selected ? "Selected" : "Select"}
      </span>
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
    <Section
      title="Competitor discovery"
      description="Suggested Amazon search results. Relevance is title and category similarity, not sales rank. You confirm what to compare."
    >
      <Panel className="p-5">
        <form onSubmit={onSubmit} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="min-w-0 flex-1 space-y-1.5">
            <Label htmlFor="amazon-search-query">Amazon search query</Label>
            <Input
              id="amazon-search-query"
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              disabled={loading}
            />
          </div>
          <Button type="submit" variant="outline" disabled={loading} className="shrink-0">
            {loading ? (
              <>
                <Loader2 className="animate-spin" />
                Searching Amazon for candidate listings…
              </>
            ) : (
              "Search Amazon"
            )}
          </Button>
        </form>
        {localError ? <p className="mt-2 text-sm text-destructive">{localError}</p> : null}
      </Panel>

      {result ? (
        <Panel>
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
            <p className="text-sm text-muted-foreground">
              Search used: {result.search_query}. Select up to {MAX_SELECTED}.
            </p>
            <p className="text-sm font-medium">
              {selectedAsins.length} / {MAX_SELECTED} selected
            </p>
          </div>
          {result.candidates.length ? (
            result.candidates.map((candidate) => (
              <CandidateRow
                key={candidate.asin}
                candidate={candidate}
                selected={selectedAsins.includes(candidate.asin)}
                disabled={selectedAsins.length >= MAX_SELECTED && !selectedAsins.includes(candidate.asin)}
                onToggle={() => onToggleAsin(candidate.asin)}
              />
            ))
          ) : (
            <p className="px-4 py-8 text-sm text-muted-foreground">
              No relevant Amazon results were found for this search.
            </p>
          )}
          <div className="px-4 py-3">
            <Button type="button" disabled={compareLoading || selectedAsins.length < 1} onClick={onCompare}>
              {compareLoading ? (
                <>
                  <Loader2 className="animate-spin" />
                  Comparing selected competitors…
                </>
              ) : (
                "Compare selected"
              )}
            </Button>
          </div>
        </Panel>
      ) : null}
    </Section>
  );
}
