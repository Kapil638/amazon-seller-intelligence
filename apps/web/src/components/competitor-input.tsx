"use client";

import { useState, type FormEvent } from "react";
import { Loader2, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Panel, Section } from "@/components/ui/layout";
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
    <Section
      title="Compare known competitors"
      description="Enter competitor ASINs directly if you already know them."
    >
      <Panel className="p-5">
        <form onSubmit={onSubmit} className="space-y-4">
          <div className="flex items-baseline justify-between gap-3 rounded-md bg-surface-subtle px-3 py-2 text-sm">
            <span className="text-xs text-muted-foreground">Your product</span>
            <span className="font-mono tracking-wide">{targetAsin}</span>
          </div>

          {asins.map((asin, index) => (
            <div key={index} className="space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor={`competitor-asin-${index}`}>Competitor ASIN {index + 1}</Label>
                {asins.length > 1 ? (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors duration-200 hover:text-foreground"
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
              className="inline-flex items-center gap-1 text-sm text-muted-foreground transition-colors duration-200 hover:text-foreground"
              onClick={() => setAsins((current) => [...current, ""])}
              disabled={loading}
            >
              <Plus className="h-4 w-4" />
              Add competitor
            </button>
          ) : null}

          {error ? <p className="text-sm text-destructive">{error}</p> : null}

          <Button type="submit" variant="outline" disabled={loading}>
            {loading ? (
              <>
                <Loader2 className="animate-spin" />
                Fetching competitor data…
              </>
            ) : (
              "Analyze competitors"
            )}
          </Button>
        </form>
      </Panel>
    </Section>
  );
}
