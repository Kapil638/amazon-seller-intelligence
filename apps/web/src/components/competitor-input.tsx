"use client";

import { useState, type FormEvent } from "react";
import { Loader2, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { isValidAsin, normalizeAsin } from "@/lib/asin";

const MAX_COMPETITORS = 3;

export function CompetitorInput({
  targetAsin,
  loading,
  onAnalyze,
}: {
  targetAsin: string;
  loading: boolean;
  onAnalyze: (asins: string[]) => void;
}) {
  const [asins, setAsins] = useState<string[]>([""]);
  const [error, setError] = useState<string | null>(null);

  function updateAsin(index: number, value: string) {
    setAsins((current) => current.map((item, i) => (i === index ? value.toUpperCase() : item)));
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = asins.map(normalizeAsin).filter(Boolean);
    const target = normalizeAsin(targetAsin);

    if (normalized.length < 1) {
      setError("Enter at least one competitor ASIN.");
      return;
    }
    if (normalized.length > MAX_COMPETITORS) {
      setError("Compare at most three competitor ASINs.");
      return;
    }
    if (normalized.some((asin) => !isValidAsin(asin))) {
      setError("Enter valid 10-character ASINs using letters and numbers only.");
      return;
    }
    if (normalized.some((asin) => asin === target)) {
      setError("The target ASIN cannot be entered as a competitor.");
      return;
    }
    if (new Set(normalized).size !== normalized.length) {
      setError("Competitor ASINs must be unique.");
      return;
    }

    setError(null);
    onAnalyze(normalized);
  }

  return (
    <Card>
      <CardHeader className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
          Competitor Intelligence
        </p>
        <CardTitle className="text-2xl">Compare Competitors</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm">
            <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Your Product</p>
            <p className="mt-1 font-mono tracking-wide">{targetAsin}</p>
          </div>

          {asins.map((asin, index) => (
            <div key={index} className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor={`competitor-asin-${index}`}>Competitor ASIN {index + 1}</Label>
                {asins.length > 1 ? (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                    onClick={() => setAsins((current) => current.filter((_, i) => i !== index))}
                    disabled={loading}
                  >
                    <X className="h-3 w-3" />
                    Remove
                  </button>
                ) : null}
              </div>
              <Input
                id={`competitor-asin-${index}`}
                value={asin}
                onChange={(event) => updateAsin(index, event.target.value)}
                placeholder="B0XXXXXXXX"
                autoComplete="off"
                spellCheck={false}
                disabled={loading}
                className="font-mono tracking-wide"
              />
            </div>
          ))}

          {asins.length < MAX_COMPETITORS ? (
            <button
              type="button"
              className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
              onClick={() => setAsins((current) => [...current, ""])}
              disabled={loading}
            >
              <Plus className="h-4 w-4" />
              Add competitor
            </button>
          ) : null}

          {error ? <p className="text-sm text-destructive">{error}</p> : null}

          <Button type="submit" size="lg" disabled={loading} className="w-full sm:w-auto">
            {loading ? (
              <>
                <Loader2 className="animate-spin" />
                Fetching competitor data...
              </>
            ) : (
              "Analyze Competitors"
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
